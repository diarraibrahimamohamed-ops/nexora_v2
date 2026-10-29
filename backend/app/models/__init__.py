"""app/models — tous les modèles SQLAlchemy Nexora v2."""
from app.database import Base  # noqa: F401

from app.models.user            import User, LoginAttempt
from app.models.sequence        import Analysis, Sequence, Mutation, AntibioticProfile, FastaFile
from app.models.docking         import DockingJob, DockingResult, ProteinMetadata, TempStructure
from app.models.ligand          import ValidatedLigand
from app.models.phytochemistry  import PhytochemicalCandidate
from app.models.evidence import ResearchEvidence
from app.models.cache           import CacheEntry
from app.models.ncbi            import NcbiSequence
from app.models.hsa             import HSAJob, QSAJob
from app.models.rna_structure_job import RnaStructureJob

__all__ = [
    "User", "LoginAttempt",
    "Analysis", "Sequence", "Mutation", "AntibioticProfile", "FastaFile",
    "DockingJob", "DockingResult", "ProteinMetadata", "TempStructure",
    "ValidatedLigand", "PhytochemicalCandidate", "ResearchEvidence", "CacheEntry", "NcbiSequence", "HSAJob", "QSAJob",
    "ResearchStudy", "ResearchVariant", "ResearchTarget", "ResearchDockingRun", "ResearchStructureJob", "ResearchStructureComparison", "ResearchDockingComparison", "ResearchScreeningRun", "ResearchScreeningCompound", "RnaStructureJob",
]

from app.models.research import ResearchStudy, ResearchVariant, ResearchTarget, ResearchDockingRun, ResearchStructureJob, ResearchStructureComparison, ResearchDockingComparison
from app.models.virtual_screening import ResearchScreeningRun, ResearchScreeningCompound
