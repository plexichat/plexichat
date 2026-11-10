"""Advanced formula evaluator, split into category mixins.

The public class name is unchanged; ``formulas.py`` still imports
``FormulaEvaluatorAdvanced`` from this package. Each mixin owns one
function family, while ``base.py`` holds the shared argument helpers.
"""

from .mixins.lookup import LookupMixin
from .mixins.text import TextMixin
from .mixins.dates import DateMixin
from .mixins.criteria import CriteriaMixin
from .mixins.structured import StructuredMixin
from .mixins.adopted import AdoptedMixin
from .mixins.financial import FinancialMixin
from .mixins.distribution import DistributionMixin
from .mixins.complex import ComplexMixin
from .mixins.engineering import EngineeringMixin


class FormulaEvaluatorAdvanced(
    LookupMixin,
    TextMixin,
    DateMixin,
    CriteriaMixin,
    StructuredMixin,
    AdoptedMixin,
    FinancialMixin,
    DistributionMixin,
    ComplexMixin,
    EngineeringMixin,
):
    """Advanced formula functions composed from category mixins."""
