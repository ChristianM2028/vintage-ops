from .click_select import ClickSelectHandler, ComboboxHandler
from .date_numeric import DateNumericHandler
from .hierarchical import HierarchicalHandler
from .rich_text import RichTextHandler
from .select import NativeSelectHandler
from .text import TextareaHandler, TextHandler
from .toggle import CheckboxHandler, ToggleHandler
from .tokenized import TokenizedHandler
from .upload import UploadHandler
from automation.models import InteractionKind

def default_handlers():
    handlers=(TextHandler(),TextareaHandler(),RichTextHandler(),NativeSelectHandler(),
              ComboboxHandler(),ClickSelectHandler(),HierarchicalHandler(),ToggleHandler(),
              CheckboxHandler(),UploadHandler(),TokenizedHandler(),DateNumericHandler())
    result={h.kind:h for h in handlers}
    result[InteractionKind.RADIO]=result[InteractionKind.CHECKBOX]
    result[InteractionKind.DROPZONE]=result[InteractionKind.FILE_INPUT]
    return result
