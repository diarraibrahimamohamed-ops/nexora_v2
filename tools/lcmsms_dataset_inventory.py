#!/usr/bin/env python3
"""Read-only K0 inventory for public MetaboLights LC-MS/MS studies.

The tool downloads ISA-Tab metadata only, lists the public archive index, and
never downloads raw instrument data. It deliberately separates machine
observations from the mandatory human reuse/metadata review.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter, deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

API_ROOT = "https://www.ebi.ac.uk/metabolights/ws"
FTP_ROOT = "https://ftp.ebi.ac.uk/pub/databases/metabolights/studies/public"
USER_AGENT = "Nexora-LCMSMS-Phase1K-Inventory/1.0 (+https://github.com/diarraibrahamimamohamed-ops/nexora_v2)"
METADATA_TYPES = {
    "metadata_investigation", "metadata_sample", "metadata_assay", "metadata_maf"
}
MAX_METADATA_BYTES = 25 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 10000
MAX_DIRECTORIES = 2000


class InventoryError(RuntimeError):
    pass


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _request_bytes(url: str, timeout: int, max_bytes: int) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, text/html, */*",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > max_bytes:
            raise InventoryError(
                f"Response exceeds configured size limit ({declared} > {max_bytes} bytes): {url}"
            )
        body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise InventoryError(f"Response exceeds configured size limit: {url}")
    return body


def _normalise_file_catalogue(payload: Any) -> list[dict[str, str]]:
    rows = payload.get("study", payload.get("files", [])) if isinstance(payload, dict) else payload
    if isinstance(rows, dict):
        rows = rows.get("files", [])
    if not isinstance(rows, list):
        raise InventoryError("Unexpected MetaboLights file-catalogue response shape.")
    result = []
    for row in rows:
        if isinstance(row, str):
            filename = row
            basename = Path(filename).name
            if basename == "i_Investigation.txt":
                file_type = "metadata_investigation"
            elif basename.startswith("s_") and basename.endswith(".txt"):
                file_type = "metadata_sample"
            elif basename.startswith("a_") and basename.endswith(".txt"):
                file_type = "metadata_assay"
            elif basename.startswith("m_") and basename.endswith(".tsv"):
                file_type = "metadata_maf"
            else:
                file_type = "unknown"
        elif isinstance(row, dict):
            filename = row.get("file") or row.get("filename") or row.get("name")
            file_type = row.get("type") or row.get("file_type") or "unknown"
        else:
            continue
        if filename:
            result.append({"file": str(filename), "type": str(file_type)})
    return result


def fetch_file_catalogue(study_id: str, timeout: int) -> dict[str, Any]:
    url = f"{API_ROOT}/studies/{study_id}/files?include_raw_data=false"
    try:
        raw = _request_bytes(url, timeout, MAX_METADATA_BYTES)
        payload = json.loads(raw.decode("utf-8-sig"))
        files = _normalise_file_catalogue(payload)
        return {"available": True, "url": url, "files": files, "error": None}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, InventoryError) as exc:
        return {
            "available": False, "url": url, "files": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def list_public_archive(
    study_id: str, *, timeout: int, max_depth: int = 4
) -> dict[str, Any]:
    root = f"{FTP_ROOT}/{study_id}/"
    parsed_root = urlparse(root)
    root_path = parsed_root.path.rstrip("/") + "/"
    pending = deque([(root, 0)])
    visited: set[str] = set()
    files: dict[str, str] = {}
    directories_seen = 0
    errors: list[str] = []

    while pending and len(files) < MAX_DIRECTORY_ENTRIES and directories_seen < MAX_DIRECTORIES:
        current, depth = pending.popleft()
        if current in visited:
            continue
        visited.add(current)
        directories_seen += 1
        try:
            html = _request_bytes(current, timeout, MAX_METADATA_BYTES).decode(
                "utf-8", errors="replace"
            )
        except (HTTPError, URLError, TimeoutError, OSError, InventoryError) as exc:
            errors.append(f"{current}: {type(exc).__name__}: {exc}")
            continue

        parser = _LinkParser()
        parser.feed(html)
        for href in parser.links:
            if href in {"../", "./", "/", ""} or href.startswith("?"):
                continue
            absolute = urljoin(current, href)
            item = urlparse(absolute)
            if item.scheme != "https" or item.netloc != parsed_root.netloc:
                continue
            if not item.path.startswith(root_path) or item.query or item.fragment:
                continue
            if absolute == current:
                continue
            if item.path.endswith("/"):
                if depth < max_depth and absolute not in visited:
                    pending.append((absolute, depth + 1))
                continue
            relative = item.path[len(root_path):]
            if relative and not relative.startswith("../"):
                files[relative] = absolute
            if len(files) >= MAX_DIRECTORY_ENTRIES:
                break

    return {
        "available": bool(visited) and bool(files),
        "root_url": root,
        "directories_scanned": directories_seen,
        "files": [{"path": path, "url": url} for path, url in sorted(files.items())],
        "errors": errors[:30],
        "truncated": bool(pending) or len(files) >= MAX_DIRECTORY_ENTRIES or directories_seen >= MAX_DIRECTORIES,
    }


def parse_tsv_text(text: str) -> list[dict[str, str]]:
    if not text.strip():
        return []
    # ISA-Tab protocol fields can exceed csv's small default field limit.
    csv.field_size_limit(MAX_METADATA_BYTES)
    reader = csv.DictReader(io.StringIO(text), delimiter="\\t")
    if not reader.fieldnames:
        return []
    return [
        {
            str(key or "").strip(): str(value or "").strip()
            for key, value in row.items()
            if key is not None
        }
        for row in reader
    ]


def _column(row: dict[str, str], *needles: str) -> str:
    wanted = [re.sub(r"[^a-z0-9]", "", item.lower()) for item in needles]
    for key in row:
        normalised = re.sub(r"[^a-z0-9]", "", key.lower())
        if any(token in normalised for token in wanted):
            return key
    return ""


def classify_sample_role(values: list[str]) -> str:
    """Return a candidate classification, never a final semantic assertion."""
    text = re.sub(r"[_-]+", " ", " ".join(values).lower())
    if re.search(r"\bpooled\s*(?:quality\s*)?control\b|\bpool(?:ed)?\s*qc\b", text):
        return "pooled_qc_candidate"
    if re.search(r"\b(?:solvent|extraction|procedural|method)\s+blank\b|\bblank\b", text):
        return "blank_candidate"
    if re.search(r"\binternal\s+standard\b|\breference\s+standard\b", text):
        return "standard_candidate"
    if re.search(r"\bquality\s+control\b|\bqc\b", text):
        return "qc_unspecified_candidate"
    return "unclassified"


def parse_sample_roles(sample_text: str) -> dict[str, str]:
    roles: dict[str, str] = {}
    for row in parse_tsv_text(sample_text):
        name_col = _column(row, "Sample Name", "SampleName")
        sample_name = row.get(name_col, "").strip() if name_col else ""
        if sample_name:
            roles[sample_name] = classify_sample_role(list(row.values()))
    return roles


def parse_assay_rows(
    assay_texts: list[tuple[str, str]],
    sample_roles: dict[str, str],
) -> list[dict[str, Any]]:
    assays: list[dict[str, Any]] = []
    for filename, text in assay_texts:
        for row_number, row in enumerate(parse_tsv_text(text), start=2):
            sample_col = _column(row, "Sample Name", "SampleName")
            raw_col = _column(row, "Raw Spectral Data File")
            derived_col = _column(row, "Derived Spectral Data File")
            assay_col = _column(row, "MS Assay Name", "Assay Name")
            order_col = _column(row, "injection order", "order of injection", "run order")
            sample_name = row.get(sample_col, "").strip() if sample_col else ""
            raw_file = row.get(raw_col, "").strip() if raw_col else ""
            derived_file = row.get(derived_col, "").strip() if derived_col else ""
            injection_order = row.get(order_col, "").strip() if order_col else ""
            raw_file = "" if raw_file.lower() in {"", "not applicable", "n/a"} else raw_file
            derived_file = "" if derived_file.lower() in {"", "not applicable", "n/a"} else derived_file
            role = sample_roles.get(sample_name) or classify_sample_role(list(row.values()))
            polarity_texts = []
            for key, value in row.items():
                key_norm = re.sub(r"[^a-z0-9]", "", key.lower())
                if any(token in key_norm for token in (
                    "polarity", "ionmode", "ionizationmode", "ionisationmode"
                )):
                    polarity_texts.append(value.lower())
            if assay_col:
                polarity_texts.append(row.get(assay_col, "").lower())
            has_positive = any(
                "positive" in value or re.search(r"(^|[^a-z])pos([^a-z]|$)", value)
                for value in polarity_texts
            )
            has_negative = any(
                "negative" in value or re.search(r"(^|[^a-z])neg([^a-z]|$)", value)
                for value in polarity_texts
            )
            polarity = (
                "positive" if has_positive and not has_negative
                else "negative" if has_negative and not has_positive
                else ""
            )
            assays.append({
                "source_assay_file": filename,
                "source_row": row_number,
                "sample_name": sample_name,
                "assay_name": row.get(assay_col, "") if assay_col else "",
                "sample_role_candidate": role,
                "polarity_candidate": polarity or "unknown",
                "injection_order_candidate": injection_order or "not_declared_in_assay",
                "raw_data_file": raw_file,
                "derived_data_file": derived_file,
                "raw_extension": Path(raw_file).suffix.lower() if raw_file else "",
                "derived_extension": Path(derived_file).suffix.lower() if derived_file else "",
            })
    return assays


def match_archive_reference(reference: str, archive_files: list[dict[str, str]]) -> dict[str, Any]:
    """Match an ISA-Tab file reference to an indexed archive path without downloading it."""
    if not reference:
        return {"status": "no_reference", "archive_paths": []}
    normalised = reference.replace("\\", "/").lstrip("./")
    exact = [
        item.get("path", "")
        for item in archive_files
        if item.get("path", "").replace("\\", "/").lstrip("./") == normalised
    ]
    if len(exact) == 1:
        return {"status": "matched_exact_path", "archive_paths": exact}
    basename = Path(normalised).name
    matches = [
        item.get("path", "")
        for item in archive_files
        if Path(item.get("path", "").replace("\\", "/")).name == basename
    ]
    if len(matches) == 1:
        return {"status": "matched_unique_basename", "archive_paths": matches}
    if len(matches) > 1:
        return {"status": "ambiguous_basename", "archive_paths": sorted(matches)}
    return {"status": "not_found_in_public_index", "archive_paths": []}


def assess_eligibility(
    *,
    study_id: str,
    catalogue_available: bool,
    archive_available: bool,
    archive_listing_complete: bool,
    metadata_types: set[str],
    downloaded_metadata_types: set[str],
    assay_rows: list[dict[str, Any]],
    archive_files: list[dict[str, str]],
    review: dict[str, Any] | None,
) -> dict[str, Any]:
    review = review or {}
    required = {"metadata_investigation", "metadata_sample", "metadata_assay"}
    metadata_complete = required.issubset(metadata_types)
    metadata_download_complete = required.issubset(downloaded_metadata_types)
    mzml_references = sum(
        1 for row in assay_rows
        if row.get("derived_extension") == ".mzml"
        or row.get("raw_extension") == ".mzml"
    )
    archive_mzml = sum(
        1 for item in archive_files
        if Path(item.get("path", "")).suffix.lower() == ".mzml"
    )
    mzml_available = mzml_references > 0 or archive_mzml > 0
    conversion = review.get("conversion_plan")
    conversion_approved = (
        isinstance(conversion, dict)
        and conversion.get("approved") is True
        and bool(str(conversion.get("tool_and_version", "")).strip())
        and bool(str(conversion.get("parameters_or_sop", "")).strip())
    )
    referenced_files = []
    for row in assay_rows:
        for reference_key, match_key in (
            ("raw_data_file", "raw_archive_match"),
            ("derived_data_file", "derived_archive_match"),
        ):
            if row.get(reference_key):
                referenced_files.append(
                    row.get(match_key, {}).get("status") in {
                        "matched_exact_path", "matched_unique_basename"
                    }
                )
    file_references_resolved = bool(referenced_files) and all(referenced_files)
    checks = {
        "metabolights_file_catalogue_retrieved": catalogue_available,
        "public_archive_file_listing_retrieved": archive_available and bool(archive_files),
        "public_archive_listing_complete": archive_listing_complete,
        "required_isa_tab_metadata_present": metadata_complete,
        "required_isa_tab_metadata_downloaded": metadata_download_complete,
        "assay_rows_retrieved": bool(assay_rows),
        "assay_file_references_resolved": file_references_resolved,
        "review_record_matches_study_accession": review.get("study_id") == study_id,
        "reuse_and_license_review_approved": (
            review.get("reuse_approved") is True
            and bool(str(review.get("license_identifier", "")).strip())
            and bool(str(review.get("license_evidence_url", "")).strip())
        ),
        "assay_file_mapping_reviewed": review.get("assay_mapping_approved") is True,
        "qc_role_mapping_reviewed": review.get("qc_role_mapping_approved") is True,
        "injection_order_reviewed": (
            review.get("injection_order_approved") is True
            and bool(str(review.get("injection_order_evidence_url", "")).strip())
        ),
        "mzml_available_or_conversion_plan_approved": mzml_available or conversion_approved,
        "review_identity_recorded": (
            bool(str(review.get("reviewed_by", "")).strip())
            and bool(str(review.get("reviewed_at_utc", "")).strip())
        ),
    }
    eligible = all(checks.values())
    return {
        "status": "eligible_for_controlled_pilot" if eligible else "blocked_pending_review",
        "eligible_for_processing": eligible,
        "checks": checks,
        "mzml_references_in_assay_metadata": mzml_references,
        "mzml_files_in_public_archive_index": archive_mzml,
        "conversion_plan_approved": conversion_approved,
        "blocking_checks": [name for name, passed in checks.items() if not passed],
    }


def build_inventory(
    study_id: str,
    catalogue: dict[str, Any],
    archive: dict[str, Any],
    metadata_records: list[dict[str, Any]],
    assay_rows: list[dict[str, Any]],
    review: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata_types = {str(item.get("type", "")) for item in catalogue.get("files", [])}
    archive_files = list(archive.get("files", []))
    for row in assay_rows:
        row["raw_archive_match"] = match_archive_reference(
            str(row.get("raw_data_file", "")), archive_files
        )
        row["derived_archive_match"] = match_archive_reference(
            str(row.get("derived_data_file", "")), archive_files
        )
    extensions = Counter(Path(item.get("path", "")).suffix.lower() for item in archive_files)
    roles = Counter(row.get("sample_role_candidate", "unclassified") for row in assay_rows)
    polarities = Counter(row.get("polarity_candidate", "unknown") for row in assay_rows)
    downloaded_metadata_types = {
        str(item.get("type", ""))
        for item in metadata_records
        if item.get("downloaded") is True
    }
    archive_listing_complete = (
        bool(archive.get("available"))
        and not archive.get("truncated", False)
        and not archive.get("errors")
    )
    eligibility = assess_eligibility(
        study_id=study_id,
        catalogue_available=bool(catalogue.get("available")),
        archive_available=bool(archive.get("available")),
        archive_listing_complete=archive_listing_complete,
        metadata_types=metadata_types,
        downloaded_metadata_types=downloaded_metadata_types,
        assay_rows=assay_rows,
        archive_files=archive_files,
        review=review,
    )
    return {
        "schema_version": "1.0.0",
        "phase": "1K",
        "stage": "K0_dataset_inventory",
        "study": {
            "accession": study_id,
            "repository": "MetaboLights",
            "study_page": f"https://www.ebi.ac.uk/metabolights/{study_id}",
            "omicsdi_record": f"https://www.omicsdi.org/dataset/metabolights_dataset/{study_id}",
            "title": "Untargeted LC-MS metabolomics of feces, intestinal contents and serum from mice",
            "public_description_observations": [
                "Untargeted reverse-phase LC-MS in positive and negative ion modes.",
                "Mouse feces, intestinal-content and serum matrices.",
                "Pooled quality-control samples and analytical blanks are described in public metadata summaries.",
                "The public summary reports raw mass-spectrometry files; exact formats require file-level inventory.",
            ],
        },
        "inventory_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "file_catalogue": catalogue,
            "public_archive": {key: value for key, value in archive.items() if key != "files"},
            "metadata_files": metadata_records,
        },
        "public_archive_file_inventory": archive_files,
        "file_extension_counts": dict(sorted(extensions.items())),
        "assay_rows": assay_rows,
        "assay_summary": {
            "row_count": len(assay_rows),
            "sample_role_candidates": dict(sorted(roles.items())),
            "polarity_candidates": dict(sorted(polarities.items())),
            "raw_file_references": sum(bool(row.get("raw_data_file")) for row in assay_rows),
            "derived_file_references": sum(bool(row.get("derived_data_file")) for row in assay_rows),
            "injection_order_declared_in_assay": sum(
                row.get("injection_order_candidate") != "not_declared_in_assay"
                for row in assay_rows
            ),
            "raw_file_references_not_found": sum(
                row.get("raw_archive_match", {}).get("status") == "not_found_in_public_index"
                for row in assay_rows
            ),
            "derived_file_references_not_found": sum(
                row.get("derived_archive_match", {}).get("status") == "not_found_in_public_index"
                for row in assay_rows
            ),
        },
        "license_review": {
            "status": "approved" if review and review.get("study_id") == study_id and review.get("reuse_approved") is True else "not_verified",
            "review_record": review,
            "policy_note": (
                "MetaboLights states that studies submitted from April 2025 are under CC0; "
                "older studies are subject to EMBL-EBI Terms of Use and submitter-specific conditions. "
                "The individual study record must be reviewed before reuse."
            ),
        },
        "eligibility": eligibility,
        "processing": {
            "raw_data_downloaded": False,
            "mzml_files_processed": False,
            "scientific_validation_claimed": False,
            "do_not_start_before_k0_pass": True,
        },
    }


def run_inventory(
    study_id: str,
    *,
    output_dir: Path,
    timeout: int,
    max_depth: int,
    review: dict[str, Any] | None,
) -> dict[str, Any]:
    catalogue = fetch_file_catalogue(study_id, timeout)
    archive = list_public_archive(study_id, timeout=timeout, max_depth=max_depth)
    metadata_dir = output_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    entries = list(catalogue.get("files", []))
    if not entries and archive.get("files"):
        for item in archive["files"]:
            name = Path(item["path"]).name
            if name == "i_Investigation.txt":
                entries.append({"file": name, "type": "metadata_investigation"})
            elif name.startswith("s_") and name.endswith(".txt"):
                entries.append({"file": name, "type": "metadata_sample"})
            elif name.startswith("a_") and name.endswith(".txt"):
                entries.append({"file": name, "type": "metadata_assay"})
            elif name.startswith("m_") and name.endswith(".tsv"):
                entries.append({"file": name, "type": "metadata_maf"})
        entries = sorted(
            {item["file"]: item for item in entries}.values(),
            key=lambda item: item["file"],
        )

    metadata_records: list[dict[str, Any]] = []
    sample_texts: list[str] = []
    assay_texts: list[tuple[str, str]] = []
    for item in entries:
        if item.get("type") not in METADATA_TYPES:
            continue
        filename = Path(str(item["file"])).name
        url = f"{API_ROOT}/studies/{study_id}/download?file={quote(filename)}"
        record: dict[str, Any] = {
            "file": filename, "type": item.get("type"), "url": url,
            "downloaded": False, "sha256": None, "error": None,
        }
        try:
            body = _request_bytes(url, timeout, MAX_METADATA_BYTES)
            (metadata_dir / filename).write_bytes(body)
            record.update({
                "downloaded": True,
                "sha256": sha256_bytes(body),
                "size_bytes": len(body),
            })
            text = body.decode("utf-8-sig", errors="replace")
            if item.get("type") == "metadata_sample":
                sample_texts.append(text)
            elif item.get("type") == "metadata_assay":
                assay_texts.append((filename, text))
        except (HTTPError, URLError, TimeoutError, OSError, InventoryError) as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        metadata_records.append(record)

    sample_roles: dict[str, str] = {}
    for text in sample_texts:
        sample_roles.update(parse_sample_roles(text))
    assay_rows = parse_assay_rows(assay_texts, sample_roles)
    report = build_inventory(
        study_id, catalogue, archive, metadata_records, assay_rows, review
    )
    report["local_paths"] = {
        "metadata_directory": str(metadata_dir),
        "report_file": str(output_dir / f"{study_id}_inventory.json"),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{study_id}_inventory.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_id", nargs="?", default="MTBLS15740")
    parser.add_argument("--output-dir", type=Path, default=Path("benchmarks/lcmsms/phase1k/reports"))
    parser.add_argument("--review-record", type=Path)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--fail-if-ineligible", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"MTBLS[0-9]+", args.study_id):
        parser.error("study_id must match MTBLS followed by digits.")
    if args.timeout < 1 or args.max_depth < 0:
        parser.error("--timeout must be positive and --max-depth non-negative.")

    review = None
    if args.review_record:
        try:
            review = json.loads(args.review_record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"Cannot read review record: {exc}")

    try:
        report = run_inventory(
            args.study_id,
            output_dir=args.output_dir,
            timeout=args.timeout,
            max_depth=args.max_depth,
            review=review,
        )
    except Exception as exc:
        print(f"Inventory failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({
        "study_id": args.study_id,
        "eligibility": report["eligibility"],
        "report_file": report["local_paths"]["report_file"],
        "metadata_files_downloaded": sum(
            1 for item in report["sources"]["metadata_files"] if item["downloaded"]
        ),
        "raw_data_downloaded": False,
    }, indent=2, ensure_ascii=False))
    if args.fail_if_ineligible and not report["eligibility"]["eligible_for_processing"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
