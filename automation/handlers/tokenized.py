from .click_select import ClickSelectHandler
from automation.models import InteractionKind
class TokenizedHandler(ClickSelectHandler): kind=InteractionKind.TOKENIZED_INPUT
