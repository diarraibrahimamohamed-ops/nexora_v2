"""app/models — tous les modèles SQLAlchemy Nexora v2."""
from app.database import Base  # noqa: F401

from app.models.user            import User, LoginAttempt
from app.models.sequence        import Analysis, Sequence, Mutation, AntibioticProfile, FastaFile
from app.models.docking         import DockingJob, DockingResult, ProteinMetadata, TempStructure
from app.models.ligand          import ValidatedLigand
from app.models.cache           import CacheEntry
from app.models.ncbi            import NcbiSequence
from app.models.hsa             import HSAJob, QSAJob

__all__ = [
    "User", "LoginAttempt",
    "Analysis", "Sequence", "Mutation", "AntibioticProfile", "FastaFile",
    "DockingJob", "DockingResult", "ProteinMetadata", "TempStructure",
    "ValidatedLigand", "CacheEntry", "NcbiSequence", "HSAJob", "QSAJob",
    "ResearchStudy", "ResearchVariant", "ResearchTarget", "ResearchDockingRun", "ResearchStructureJob", "ResearchStructureComparison", "ResearchDockingComparison", "ResearchScreeningRun", "ResearchScreeningCompound",
]

from app.models.research import ResearchStudy, ResearchVariant, ResearchTarget, ResearchDockingRun, ResearchStructureJob, ResearchStructureComparison, ResearchDockingComparison
from app.models.virtual_screening import ResearchScreeningRun, ResearchScreeningCompound
