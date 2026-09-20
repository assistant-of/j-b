"""Small, closed JSON schemas shared by providers and local validation."""


def obj(**fields):
    return {"type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False}


def arr(items, minimum=1):
    return {"type": "array", "items": items, "minItems": minimum}


STRING = {"type": "string", "minLength": 1}
EVIDENCE = obj(source=STRING, quote=STRING)
CLAIM = obj(text=STRING, evidence=arr(EVIDENCE))
REQUIREMENT = obj(id=STRING, keyword=STRING, job_quote=STRING,
                  section=STRING)
PLAN = obj(company=STRING, role=STRING, requirements=arr(REQUIREMENT),
           gaps=arr(STRING, 0))
OUTLINE = obj(candidates=arr(obj(name=STRING, rationale=STRING,
    sections=arr(obj(title=STRING, entries=arr(STRING))))))
BULLET = obj(text=STRING, evidence=arr(EVIDENCE), requirements=arr(STRING))
ENTRY = obj(title=CLAIM, detail=CLAIM, bullets=arr(BULLET, 0))
CANDIDATE = obj(name=STRING, header=arr(CLAIM),
    sections=arr(obj(title=STRING, entries=arr(ENTRY))),
    cover_letter=arr(CLAIM))
DRAFT = obj(candidates=arr(CANDIDATE))
RANKING = obj(listings=arr(obj(file=STRING, score={"type": "integer", "minimum": 0,
    "maximum": 100}, reasons=arr(STRING), gaps=arr(STRING, 0)), 0),
    suggested_searches=arr(STRING, 0))


def validate(value, schema, path="$"):
    kind = schema["type"]
    types = {"object": dict, "array": list, "string": str, "integer": int}
    if type(value) is not types[kind]:
        raise ValueError(f"{path}: expected {kind}")
    if kind == "object":
        if set(value) != set(schema["properties"]):
            raise ValueError(f"{path}: fields must be {list(schema['properties'])}")
        for key, child in schema["properties"].items():
            validate(value[key], child, f"{path}.{key}")
    elif kind == "array":
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path}: too few items")
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{i}]")
    elif kind == "string" and len(value.strip()) < schema.get("minLength", 0):
        raise ValueError(f"{path}: empty text")
    elif kind == "integer" and not schema.get("minimum", value) <= value <= schema.get("maximum", value):
        raise ValueError(f"{path}: out of range")
