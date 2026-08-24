"""Small, UI-neutral view model for eBay category metadata."""

CONDITION_ENUMS = {
    "1000":"NEW", "1500":"NEW_OTHER", "1750":"NEW_WITH_DEFECTS",
    "2000":"CERTIFIED_REFURBISHED", "2010":"EXCELLENT_REFURBISHED",
    "2020":"VERY_GOOD_REFURBISHED", "2030":"GOOD_REFURBISHED",
    "2500":"SELLER_REFURBISHED", "2750":"LIKE_NEW", "3000":"USED_EXCELLENT",
    "4000":"USED_VERY_GOOD", "5000":"USED_GOOD", "6000":"USED_ACCEPTABLE",
    "7000":"FOR_PARTS_OR_NOT_WORKING",
}

def parse_aspects(payload):
    result=[]
    for raw in payload.get("aspects",[]):
        name=raw.get("localizedAspectName")
        if not name: continue
        constraint=raw.get("aspectConstraint") or {}
        mode=(constraint.get("aspectMode") or "FREE_TEXT").upper()
        cardinality=(constraint.get("itemToAspectCardinality") or "SINGLE").upper()
        values=[x.get("localizedValue") for x in raw.get("aspectValues",[]) if x.get("localizedValue")]
        required=bool(constraint.get("aspectRequired"))
        recommended=(constraint.get("aspectUsage") or raw.get("aspectUsage") or "").upper()=="RECOMMENDED"
        result.append({"name":name,"required":required,"recommended":recommended and not required,
            "values":values,"control":"select" if values else "text","free_text":mode in {"FREE_TEXT","SELECTION_ONLY_WITH_CUSTOM_VALUE"},
            "multiple":cardinality=="MULTI","mode":mode,"cardinality":cardinality})
    return result

def parse_conditions(payload):
    policies=payload.get("itemConditionPolicies") or payload.get("conditionPolicies") or []
    result=[]
    expanded=[]
    for policy in policies:
        expanded.extend(policy.get("itemConditions") or [policy])
    for policy in expanded:
        cid=str(policy.get("conditionId", ""))
        enum=CONDITION_ENUMS.get(cid)
        if not enum: continue
        descriptors=policy.get("conditionDescriptors") or []
        result.append({"value":enum,"condition_id":cid,"name":policy.get("conditionDescription") or enum.replace("_"," ").title(),
                       "descriptors":descriptors,"requires_descriptors":bool(descriptors)})
    return result

def validate_specifics(fields, submitted):
    by_name={f["name"]:f for f in fields}; normalized={}; errors=[]
    for name, values in submitted.items():
        if name not in by_name: errors.append(f"Unknown item specific: {name}."); continue
        field=by_name[name]; values=[str(v).strip() for v in values if str(v).strip()]
        if len(values)>1 and not field["multiple"]: errors.append(f"{name} accepts only one value."); continue
        if field["values"]:
            invalid=[v for v in values if v not in field["values"]]
            if invalid and not field["free_text"]: errors.append(f"Choose an allowed value for {name}."); continue
        elif values and not field["free_text"]:
            errors.append(f"{name} does not permit free text."); continue
        if values: normalized[name]=values
    for field in fields:
        if field["required"] and not normalized.get(field["name"]): errors.append(f"{field['name']} is required for this category.")
    return normalized,errors
