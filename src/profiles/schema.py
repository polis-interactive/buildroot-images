"""Update schema choices from Python enums: python -m src.profiles.schema [--check]."""
import argparse
import json
from pathlib import Path

from .options import Base, Capability, Group, BASE_GROUPS, PROFILE_GROUPS

SCHEMAS = Path(__file__).resolve().parents[2] / "docs/build-profiles"


def choices(values):
    return {"type": "string", "enum": [item.value for item in values]}


def draft4(value):
    """Keep TOML schemas compatible with Taplo's documented Draft 4 support."""
    if isinstance(value, list):
        return [draft4(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "$defs":
            key = "definitions"
        elif key == "const":
            key, item = "enum", [item]
        elif key == "$ref":
            item = item.replace("#/$defs/", "#/definitions/")
        result[key] = draft4(item)
    return result


def schema_documents():
    """Structural fields are handwritten; enum fields are generated."""
    documents = {name: json.loads((SCHEMAS / f"{name}-v1.schema.json").read_text())
                 for name in ("profile", "base", "audit")}
    profile = documents["profile"]
    profile["description"] = (
        "Profile choices come from src/profiles/options.py. "
        "Dependency and capability resolution must also succeed before building.")
    profile["properties"]["base"] = choices(Base)
    for field, values in (("requires", Capability), ("groups", PROFILE_GROUPS)):
        profile["properties"][field] = {
            "type": "array", "uniqueItems": True, "items": choices(values)}
    profile.get("$defs", profile.get("definitions", {})).pop("ids", None)
    base = documents["base"]
    base["properties"]["name"] = choices(Base)
    base["properties"]["groups"]["items"] = choices(BASE_GROUPS)
    audit = documents["audit"]
    audit["properties"]["base"] = choices(Base)
    audit["$defs"]["group"]["properties"]["id"] = choices(Group)
    audit["$defs"]["change"]["properties"]["owners"]["items"] = choices(Group)
    for name in ("profile", "base"):
        documents[name] = draft4(documents[name])
        documents[name]["$schema"] = "http://json-schema.org/draft-04/schema#"
    for document in documents.values():
        document["$comment"] = (
            "Enum choices are generated from src/profiles/options.py. "
            "Run python -m src.profiles.schema after changing them.")
    return documents


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if enum choices need regeneration")
    args = parser.parse_args(argv)
    stale = []
    for name, document in schema_documents().items():
        path = SCHEMAS / f"{name}-v1.schema.json"
        if json.loads(path.read_text()) != document:
            stale.append(path.name)
            if not args.check:
                path.write_text(json.dumps(document, indent=2) + "\n")
    if args.check and stale:
        print("Stale schema choices: " + ", ".join(stale))
        print("Run python -m src.profiles.schema")
        return 1
    print("Profile schema choices match Python enums." if args.check else "Profile schema choices updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
