from sqlalchemy import Column, Integer, String, Text, DateTime, Enum as SAEnum, Numeric
from app.database import Base
from datetime import datetime
import enum


class LigandStatus(str, enum.Enum):
    approved       = "approved"
    experimental   = "experimental"
    clinical_trial = "clinical_trial"
    research       = "research"


class ValidatedLigand(Base):
    __tablename__ = "validated_ligands"
    id                             = Column(Integer,    primary_key=True, index=True)
    name                           = Column(String(200), nullable=False)
    smiles                         = Column(String(500), nullable=False, unique=True)
    molecular_weight               = Column(Numeric(10,2), nullable=False)
    logp                           = Column(Numeric(4,2),  nullable=False)
    hydrogen_bond_donors           = Column(Integer,       nullable=False)
    hydrogen_bond_acceptors        = Column(Integer,       nullable=False)
    rotatable_bonds                = Column(Integer,       nullable=False)
    topological_polar_surface_area = Column(Numeric(8,2),  nullable=False)
    drug_likeness_score            = Column(Numeric(5,3),  nullable=False)
    category                       = Column(String(100),   nullable=False, index=True)
    description                    = Column(Text,          nullable=True)
    pubchem_cid                    = Column(Integer,       nullable=True)
    chebi_id                       = Column(String(50),    nullable=True)
    uniprot_target                 = Column(String(50),    nullable=True)
    binding_affinity_kcal          = Column(Numeric(6,3),  nullable=True)
    status                         = Column(SAEnum(LigandStatus), default=LigandStatus.research, index=True)
    created_at                     = Column(DateTime, default=datetime.utcnow)
    updated_at                     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
