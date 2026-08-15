from .text import TextHandler
from automation.models import InteractionKind
class DateNumericHandler(TextHandler): kind=InteractionKind.DATE_OR_NUMERIC
