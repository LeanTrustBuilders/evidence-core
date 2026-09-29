"""Pure functions over LeanTrustBuilders datasets (S2) and evidence records (S3).

Standard library only. See the README for the specifications this implements.
"""
from .dataset import Dataset, Decl
from .records import canonical, record_id, with_id, validate, subject_from_decl
from .status import classify, Status, changed_underneath
from .coverage import Evidence, Policy, Coverage, coverage, queue
from .store import Store, StoreError, Imported, with_imports
from .rubric import Rubric, Axis, STANDARD as STANDARD_RUBRIC

__version__ = "0.16.1"
