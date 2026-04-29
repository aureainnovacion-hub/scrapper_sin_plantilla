"""
models.py
---------
Definición del modelo ORM (SQLAlchemy) para la tabla `leads`.
"""

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Float,
    Boolean,
    create_engine,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.orm import declarative_base, Session

Base = declarative_base()


class Lead(Base):
    """Representa una empresa extraída del scraping."""

    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nombre = Column(String(255), nullable=False)
    web = Column(String(512), nullable=True)
    nif = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)
    telefono = Column(String(50), nullable=True)
    fuente = Column(String(100), nullable=True)
    keyword = Column(String(100), nullable=True)
    fecha_scraping = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Campos de subvenciones
    total_subvenciones = Column(Float, default=0.0, nullable=True)
    num_concesiones = Column(Integer, default=0, nullable=True)
    es_prioritario = Column(Boolean, default=False, nullable=True)

    # Campo de seguimiento comercial
    contactado = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("nombre", "fuente", name="uq_nombre_fuente"),
    )

    def __repr__(self) -> str:
        return (
            f"<Lead id={self.id} nombre='{self.nombre}' "
            f"email='{self.email}' telefono='{self.telefono}' contactado={self.contactado}>"
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "nombre": self.nombre,
            "web": self.web,
            "nif": self.nif,
            "email": self.email,
            "telefono": self.telefono,
            "fuente": self.fuente,
            "keyword": self.keyword,
            "fecha_scraping": (
                self.fecha_scraping.isoformat() if self.fecha_scraping else None
            ),
            "total_subvenciones": self.total_subvenciones,
            "num_concesiones": self.num_concesiones,
            "es_prioritario": self.es_prioritario,
            "contactado": self.contactado,
        }


def init_db(database_url: str) -> Session:
    engine = create_engine(database_url, echo=False)
    Base.metadata.create_all(engine)

    # Migración automática: añadir columna telefono si no existe
    with engine.connect() as conn:
        inspector = inspect(engine)
        cols = [c["name"] for c in inspector.get_columns("leads")]
        if "telefono" not in cols:
            conn.execute(text("ALTER TABLE leads ADD COLUMN telefono VARCHAR(50)"))
            conn.commit()

    return Session(engine)
