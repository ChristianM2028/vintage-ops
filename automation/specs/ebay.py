from automation.models import (FieldSpec,FieldValue,InteractionKind,
    VerificationMode,VerificationSpec)

CHOICE=frozenset({InteractionKind.NATIVE_SELECT,InteractionKind.COMBOBOX,InteractionKind.CLICK_SELECT})
TEXT=frozenset({InteractionKind.TEXTBOX,InteractionKind.TEXTAREA})

def _spec(key,aliases,value,kinds,*,required=True,deps=(),mode=VerificationMode.NORMALIZED_TEXT,probe=True):
    return FieldSpec(key,aliases,value,required,kinds,VerificationSpec(mode,value.expected),deps,allow_probe=probe,permit_omission=not required)

def create_listing_specs(item):
    """Immutable values mapped onto labels visible in capture 2026-08-capture-1."""
    photos=tuple(p.get("path") or p.get("filename") for p in item.get("photos",()) if p.get("path") or p.get("filename"))
    specs=[
      _spec("listing_query",("Start your listing","Tell us what you want to sell"),FieldValue(text=item["title"]),frozenset({InteractionKind.TEXTBOX}),probe=False),
      _spec("category",("Category","Item category"),FieldValue(category_path=tuple(x.strip() for x in item["category"].split(">") if x.strip())),frozenset({InteractionKind.HIERARCHICAL_SELECT,InteractionKind.CLICK_SELECT})),
      _spec("condition",("Select condition","Item condition","Condition"),FieldValue(choice=item["condition"]),CHOICE,deps=("category",)),
    ]
    if photos: specs.append(_spec("photos",("Photos & video","Upload from computer"),FieldValue(photo_paths=photos),frozenset({InteractionKind.FILE_INPUT,InteractionKind.DROPZONE}),deps=("condition",),mode=VerificationMode.FILES))
    specs.extend([
      _spec("title",("Item title","Title"),FieldValue(text=item["title"]),frozenset({InteractionKind.TEXTBOX}),deps=("category",),probe=False),
      _spec("brand",("Brand",),FieldValue(choice=item.get("brand") or "Unbranded"),CHOICE,deps=("category",)),
      _spec("color",("Color",),FieldValue(choice=item.get("color") or "Multicolor"),CHOICE,deps=("category",)),
      _spec("size",("Size",),FieldValue(choice=item.get("size") or "Does not apply"),CHOICE,deps=("category",)),
      _spec("condition_description",("Condition description",),FieldValue(text=item.get("condition_description") or item["description"]),frozenset({InteractionKind.TEXTAREA}),required=False,deps=("condition",),probe=False),
      _spec("description",("Description","Write a detailed description of your item"),FieldValue(text=item["description"]),frozenset({InteractionKind.TEXTAREA,InteractionKind.RICH_TEXT}),deps=("condition",)),
      _spec("price",("Price","Buy It Now price","Starting bid"),FieldValue(number=float(item["price"])),frozenset({InteractionKind.DATE_OR_NUMERIC,InteractionKind.TEXTBOX}),deps=("category",)),
    ])
    return tuple(specs)
