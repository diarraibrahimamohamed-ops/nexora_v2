/* N3XORA Experimental Analytics UI — isolated from the main research page script. */
(() => {
  let pendingMeasurementImportSha = null;

  const byId = id => document.getElementById(id);
  const currentAssayContext = () => ({
    studyId: currentStudy?.study?.id,
    assayId: activeExperimentalAssayId
  });

  async function previewMeasurements(event) {
    event.preventDefault();
    const {studyId, assayId} = currentAssayContext();
    const file = byId("assayImportFile").files[0];
    const preview = byId("assayImportPreview");
    const confirm = byId("confirmAssayImport");
    pendingMeasurementImportSha = null;
    confirm.disabled = true;
    confirm.hidden = true;
    if (!studyId || !assayId) return show("Sélectionne un dossier expérimental.", false);
    if (!file) return show("Sélectionne un fichier CSV, TSV ou XLSX.", false);

    preview.textContent = "Lecture du fichier et contrôle des colonnes…";
    try {
      const result = await api.previewResearchAssayMeasurementImport(studyId, assayId, file);
      pendingMeasurementImportSha = result.source_sha256;
      const errors = (result.errors || []).map(row =>
        '<div class="text-red-300">Ligne ' + escapeHtml(row.row) + ' · ' + escapeHtml(row.message) + '</div>'
      ).join("");
      const rows = (result.preview || []).map(row =>
        '<tr><td>' + escapeHtml(row.sample_role) + '</td>' +
        '<td>' + escapeHtml(row.replicate_id) + ' · ' + escapeHtml(row.replicate_kind) + '</td>' +
        '<td>' + escapeHtml(row.biological_sample_id || "—") + '</td>' +
        '<td>' + escapeHtml(row.concentration_value ?? "—") + ' ' + escapeHtml(row.concentration_unit || "") + '</td>' +
        '<td>' + escapeHtml(row.response_name) + '</td>' +
        '<td>' + escapeHtml(row.response_value) + ' ' + escapeHtml(row.response_unit) + '</td></tr>'
      ).join("");

      preview.innerHTML =
        '<div class="mb-2">Lignes valides : <b>' + result.row_count +
        '</b> · erreurs : <b>' + result.error_count +
        '</b> · SHA-256 : <code>' + escapeHtml(result.source_sha256) + '</code></div>' +
        errors +
        (rows ? '<div class="overflow-x-auto"><table class="w-full"><thead><tr>' +
          '<th>Rôle</th><th>Réplicat</th><th>Parent biologique</th><th>Concentration</th><th>Readout</th><th>Réponse</th>' +
          '</tr></thead><tbody>' + rows + '</tbody></table></div>' : "") +
        (result.row_count > 15 ?
          '<div class="text-gray-500">Aperçu des 15 premières lignes ; le fichier complet sera importé après confirmation.</div>' : "");

      if (result.error_count === 0 && result.row_count > 0) {
        confirm.hidden = false;
        confirm.disabled = false;
      } else {
        preview.innerHTML += '<div class="text-amber-300 mt-2">Corrige les erreurs avant de confirmer l’import.</div>';
      }
    } catch (error) {
      preview.textContent = "Prévisualisation refusée : " + error.message;
      show(error.message, false);
    }
  }

  async function confirmMeasurementsImport() {
    const {studyId, assayId} = currentAssayContext();
    const file = byId("assayImportFile").files[0];
    const button = byId("confirmAssayImport");
    if (!studyId || !assayId || !pendingMeasurementImportSha) {
      return show("Prévisualise d’abord le fichier.", false);
    }
    if (!file) return show("Sélectionne à nouveau le fichier aperçu.", false);

    button.disabled = true;
    try {
      const result = await api.importResearchAssayMeasurements(
        studyId, assayId, file, pendingMeasurementImportSha
      );
      show(result.created_count + " mesure(s) importée(s). Le fichier source est archivé avec SHA-256.");
      byId("assayImportFile").value = "";
      pendingMeasurementImportSha = null;
      await loadExperimentalAssays();
      await selectExperimentalAssay(assayId);
    } catch (error) {
      show(error.message, false);
      button.disabled = false;
    }
  }

  function renderDoseResponseAnalysis(result) {
    const ci = result.confidence_interval || {};
    const stats = result.fit_statistics || {};
    const summary = result.data_summary || {};
    const confidenceText = ci.status === "available"
      ? "IC 95 % : [" + Number(ci.lower).toPrecision(5) + ", " +
        Number(ci.upper).toPrecision(5) + "] " + escapeHtml(result.concentration_unit)
      : "IC 95 % non estimable : " + escapeHtml(ci.reason || "données insuffisantes");

    const warnings = (result.warnings || []).map(item =>
      '<li class="text-amber-300">' + escapeHtml(item) + '</li>'
    ).join("");
    const observed = result.concentration_summary || [];
    const curve = result.fitted_curve || [];
    const allY = observed.map(item => Number(item.mean_response))
      .concat(curve.map(item => Number(item.predicted_response)));
    const yMin = Math.min(...allY);
    const yMax = Math.max(...allY);
    const ySpan = (yMax - yMin) || 1;
    const xMin = Math.log10(Number(summary.concentration_min));
    const xMax = Math.log10(Number(summary.concentration_max));
    const x = value => 50 + (Math.log10(Number(value)) - xMin) / ((xMax - xMin) || 1) * 560;
    const y = value => 205 - (Number(value) - yMin) / ySpan * 165;
    const path = curve.map((point, index) =>
      (index ? "L" : "M") + x(point.concentration).toFixed(2) + "," +
      y(point.predicted_response).toFixed(2)
    ).join(" ");
    const dots = observed.map(point =>
      '<circle cx="' + x(point.concentration).toFixed(2) + '" cy="' +
      y(point.mean_response).toFixed(2) + '" r="4" fill="#67e8f9">' +
      '<title>' + escapeHtml(point.concentration) + " " +
      escapeHtml(point.concentration_unit) + " : " +
      escapeHtml(point.mean_response) + " " + escapeHtml(point.response_unit) +
      '</title></circle>'
    ).join("");
    const svg =
      '<svg viewBox="0 0 640 245" role="img" aria-label="Courbe dose-réponse ajustée" class="w-full max-w-3xl bg-black bg-opacity-20 rounded mt-3">' +
      '<path d="M50 25V205H610" stroke="#64748b" fill="none"/>' +
      '<path d="' + path + '" stroke="#34d399" stroke-width="3" fill="none"/>' + dots +
      '<text x="50" y="226" fill="#94a3b8" font-size="10">' +
      escapeHtml(summary.concentration_min) + " " + escapeHtml(result.concentration_unit) + '</text>' +
      '<text x="540" y="226" fill="#94a3b8" font-size="10">' +
      escapeHtml(summary.concentration_max) + " " + escapeHtml(result.concentration_unit) + '</text>' +
      '<text x="55" y="15" fill="#94a3b8" font-size="10">' +
      escapeHtml(result.response_name) + " · " + escapeHtml(result.response_unit) + '</text></svg>';

    byId("doseResponseResult").innerHTML =
      '<div class="border border-emerald-800 rounded p-3">' +
      '<div class="text-emerald-300 font-bold text-base">' +
      escapeHtml(result.endpoint_name) + " = " +
      Number(result.estimate).toPrecision(5) + " " + escapeHtml(result.concentration_unit) + '</div>' +
      '<div class="mt-1">' + confidenceText + '</div>' +
      '<div class="text-gray-400 mt-1">Pente de Hill : ' + Number(result.hill_slope).toFixed(3) +
      " · R² : " + (stats.r_squared == null ? "non défini" : Number(stats.r_squared).toFixed(4)) +
      " · RMSE : " + Number(stats.rmse).toFixed(4) + '</div>' +
      '<div class="text-gray-400 mt-1">Concentrations : ' + summary.distinct_concentrations +
      " · réplicats biologiques : " + summary.biological_replicates_total +
      " · complets : " + summary.complete_biological_replicates +
      " · observations ajustées : " + stats.observations_used + '</div>' +
      svg + (warnings ? '<ul class="list-disc pl-5 mt-2">' + warnings + '</ul>' : "") +
      '<p class="text-gray-500 mt-2">' + escapeHtml(result.interpretation_limit) + '</p>' +
      '<div class="text-gray-500 mt-1">SHA-256 des données analysées : <code>' +
      escapeHtml(result.input_sha256) + '</code></div></div>';
  }

  function renderAdvancedDoseResponseAnalysis(result) {
    const ci = result.confidence_interval || {};
    const fit = result.fit || {};
    const quality = result.quality || {};
    const sample = result.sample || {};
    const model = result.model || {};
    const confidenceText = Number.isFinite(Number(ci.lower)) && Number.isFinite(Number(ci.upper))
      ? "IC 95 % : [" + Number(ci.lower).toPrecision(5) + ", " +
        Number(ci.upper).toPrecision(5) + "] " + escapeHtml(fit.concentration_unit || "")
      : "IC 95 % non estimable : " + escapeHtml(ci.reason || "bootstrap insuffisant");

    const warnings = (quality.warnings || []).map(item =>
      '<li class="text-amber-300">' + escapeHtml(item) + '</li>'
    ).join("");
    const observed = result.dose_summary || [];
    const curve = result.fitted_curve || [];
    const allY = observed.map(item => Number(item.mean_response))
      .concat(curve.map(item => Number(item.fitted_response)));
    const safeY = allY.filter(Number.isFinite);
    const yMin = safeY.length ? Math.min(...safeY) : 0;
    const yMax = safeY.length ? Math.max(...safeY) : 1;
    const ySpan = (yMax - yMin) || 1;
    const xMin = Math.log10(Number(sample.concentration_min));
    const xMax = Math.log10(Number(sample.concentration_max));
    const x = value => 50 + (Math.log10(Number(value)) - xMin) / ((xMax - xMin) || 1) * 560;
    const y = value => 205 - (Number(value) - yMin) / ySpan * 165;
    const path = curve.map((point, index) =>
      (index ? "L" : "M") + x(point.concentration).toFixed(2) + "," +
      y(point.fitted_response).toFixed(2)
    ).join(" ");
    const dots = observed.map(point =>
      '<circle cx="' + x(point.concentration).toFixed(2) + '" cy="' +
      y(point.mean_response).toFixed(2) + '" r="4" fill="#67e8f9">' +
      '<title>' + escapeHtml(point.concentration) + " " +
      escapeHtml(point.concentration_unit) + " : " +
      escapeHtml(point.mean_response) + '</title></circle>'
    ).join("");
    const svg = path
      ? '<svg viewBox="0 0 640 245" role="img" aria-label="Courbe dose-réponse 4PL" class="w-full max-w-3xl bg-black bg-opacity-20 rounded mt-3">' +
        '<path d="M50 25V205H610" stroke="#64748b" fill="none"/>' +
        '<path d="' + path + '" stroke="#34d399" stroke-width="3" fill="none"/>' + dots +
        '<text x="50" y="226" fill="#94a3b8" font-size="10">' +
        escapeHtml(sample.concentration_min) + " " + escapeHtml(fit.concentration_unit) + '</text>' +
        '<text x="540" y="226" fill="#94a3b8" font-size="10">' +
        escapeHtml(sample.concentration_max) + " " + escapeHtml(fit.concentration_unit) + '</text></svg>'
      : "";

    byId("doseResponseResult").innerHTML =
      '<div class="border border-emerald-800 rounded p-3">' +
      '<div class="flex flex-wrap items-center gap-2"><b class="text-emerald-300 text-base">' +
      escapeHtml(model.endpoint_label || "Midpoint 4PL") + " = " +
      Number(fit.midpoint_concentration).toPrecision(5) + " " +
      escapeHtml(fit.concentration_unit || "") + '</b><span class="badge">' +
      escapeHtml(quality.status || "non évalué") + '</span></div>' +
      '<div class="mt-1">' + confidenceText + '</div>' +
      '<div class="text-gray-400 mt-1">Pente de Hill : ' + Number(fit.hill_slope).toFixed(3) +
      " · R² : " + (fit.r_squared == null ? "non défini" : Number(fit.r_squared).toFixed(4)) +
      " · RMSE : " + (fit.rmse == null ? "non défini" : Number(fit.rmse).toFixed(4)) + '</div>' +
      '<div class="text-gray-400 mt-1">Concentrations : ' + escapeHtml(sample.n_concentrations) +
      " · réplicats biologiques complets : " + escapeHtml(sample.n_biological_replicates) +
      " · mesures techniques agrégées : " + escapeHtml(sample.n_technical_measurements_used) +
      " · lignes d’essais indépendants exclues : " + escapeHtml(sample.n_independent_run_rows_not_used) + '</div>' +
      svg + (warnings ? '<ul class="list-disc pl-5 mt-2">' + warnings + '</ul>' : "") +
      '<p class="text-gray-400 mt-2">' +
      'Le midpoint 4PL est relatif aux deux asymptotes ajustées ; il ne représente pas nécessairement 50 % de réponse brute. ' +
      'Il s’agit d’une estimation exploratoire conditionnelle au modèle et au protocole.</p>' +
      '<p class="text-gray-500 mt-1">SHA-256 entrée : <code>' +
      escapeHtml(result.provenance?.input_sha256 || "") + '</code></p></div>';
  }

  async function runDoseResponseAnalysis(event) {
    event.preventDefault();
    const {studyId, assayId} = currentAssayContext();
    if (!studyId || !assayId) return show("Sélectionne un essai accepté pour analyse.", false);
    const value = id => byId(id).value.trim();
    const timepoint = value("doseResponseTimepoint");
    const timeUnit = value("doseResponseTimepointUnit");
    if ((timepoint === "") !== (timeUnit === "")) {
      return show("Renseigne ensemble le timepoint et son unité.", false);
    }
    const payload = {
      response_name: value("doseResponseReadout"),
      response_unit: value("doseResponseResponseUnit"),
      concentration_unit: value("doseResponseConcentrationUnit"),
      response_direction: value("doseResponseDirection"),
      endpoint_name: value("doseResponseEndpoint"),
      sample_label: value("doseResponseSampleLabel") || null,
      bootstrap_iterations: Number(value("doseResponseBootstrap")) || 1000,
      timepoint_value: timepoint === "" ? null : Number(timepoint),
      timepoint_unit: timeUnit || null
    };
    if (!payload.response_unit || !payload.concentration_unit) {
      return show("Renseigne les unités exactes du readout et de la concentration.", false);
    }
    if (payload.bootstrap_iterations < 500 || payload.bootstrap_iterations > 2000) {
      return show("Le bootstrap doit être compris entre 500 et 2 000 itérations.", false);
    }
    const output = byId("doseResponseResult");
    output.textContent = "Ajustement 4PL et bootstrap en cours…";
    try {
      const response = await api.analyzeResearchAssayDoseResponseAdvanced(studyId, assayId, payload);
      renderAdvancedDoseResponseAnalysis(response.analysis.result);
      await loadAssayAnalyticsHistory();
      await loadExperimentalAssays();
    } catch (error) {
      output.textContent = "Analyse impossible : " + error.message;
      show(error.message, false);
    }
  }

  async function loadAssayAnalyticsHistory() {
    const {studyId, assayId} = currentAssayContext();
    if (!studyId || !assayId) return;
    const element = byId("assayAnalyticsHistory");
    try {
      const response = await api.listResearchAssayAnalyses(studyId, assayId);
      const items = response.analyses || [];
      element.innerHTML = '<div class="font-bold text-emerald-300">Analyses enregistrées (' +
        items.length + ')</div>' + items.map(item => {
          const result = item.result || {};
          const estimate = result.fit?.midpoint_concentration ?? result.estimate;
          const unit = result.fit?.concentration_unit ?? result.concentration_unit ?? "";
          const endpoint = result.model?.endpoint_label ?? result.endpoint_name ?? item.analysis_type;
          const status = result.quality?.status ?? result.confidence_interval?.status ?? item.status ?? "";
          const rank = result.rank_comparison?.rho;
          const detail = Number.isFinite(Number(rank))
            ? "ρ Spearman " + Number(rank).toFixed(3)
            : (Number.isFinite(Number(estimate)) ? endpoint + " " + Number(estimate).toPrecision(5) + " " + unit : endpoint);
          return '<div class="border border-gray-800 rounded p-2 mt-1">#' + item.id +
            " · " + escapeHtml(item.algorithm || item.analysis_type) + " v" + escapeHtml(item.algorithm_version) +
            " · " + escapeHtml(detail) + " · " + escapeHtml(status) + '</div>';
        }).join("");
    } catch (error) {
      element.textContent = "Historique statistique indisponible : " + error.message;
    }
  }

  const replicateKindInput = byId("measurementReplicateKind");
  const biologicalParentInput = byId("measurementBiologicalSampleId");
  function syncBiologicalParentRequirement() {
    const required = replicateKindInput.value === "technical";
    biologicalParentInput.required = required;
    biologicalParentInput.disabled = !required;
    if (!required) biologicalParentInput.value = "";
  }
  replicateKindInput.addEventListener("change", syncBiologicalParentRequirement);
  byId("assayMeasurementEntry").addEventListener("reset", () => {
    window.setTimeout(syncBiologicalParentRequirement, 0);
  });
  syncBiologicalParentRequirement();

  document.getElementById("assayImportEntry").addEventListener("submit", previewMeasurements);
  document.getElementById("confirmAssayImport").addEventListener("click", confirmMeasurementsImport);
  document.getElementById("doseResponseAnalysisForm").addEventListener("submit", runDoseResponseAnalysis);

  // The page's main script defines the research workflow first. Wrap its assay
  // selector to refresh the analysis history once an accepted dossier is opened.
  if (typeof window.selectExperimentalAssay === "function") {
    const originalSelectAssay = window.selectExperimentalAssay;
    window.selectExperimentalAssay = async function (assayId) {
      await originalSelectAssay(assayId);
      const context = currentAssayContext();
      if (!context.studyId || !context.assayId) return;
      try {
        const response = await api.getResearchAssay(context.studyId, context.assayId);
        const assay = response.assay;
        const editable = assay.status === "draft";
        const analysable = assay.status === "reviewed" && assay.review_decision === "accepted_for_analysis";
        byId("assayImportPanel").classList.toggle("hidden", !editable);
        byId("doseResponseAnalysisPanel").classList.toggle("hidden", !analysable);
        pendingMeasurementImportSha = null;
        if (analysable) await loadAssayAnalyticsHistory();
      } catch (error) {
        show(error.message, false);
      }
    };
  }
})();
