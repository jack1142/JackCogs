from collections.abc import Sequence
from pathlib import Path
import json
import re
import sys
import typing

from redbot import VersionInfo
from strictyaml import (
    load as yaml_load,
    Bool,
    EmptyDict,
    EmptyList,
    Enum,
    Map,
    MapPattern,
    Optional,
    Regex,
    ScalarValidator,
    Seq,
    Str,
    Url,
)
from strictyaml.exceptions import YAMLValidationError, YAMLSerializationError
from strictyaml.utils import is_string
from strictyaml.yamllocation import YAMLChunk


ROOT_PATH = Path(__file__).absolute().parent.parent


class PythonVersion(ScalarValidator):
    REGEX = re.compile(r"(\d+)\.(\d+)\.(\d+)")

    def __init__(self) -> None:
        self._matching_message = "when expecting Python version (MAJOR.MINOR.MICRO)"

    def validate_scalar(self, chunk: YAMLChunk) -> typing.List[int]:
        match = self.REGEX.fullmatch(chunk.contents)
        if match is None:
            raise YAMLValidationError(
                self._matching_message, "found non-matching string", chunk
            )
        return [int(group) for group in match.group(1, 2, 3)]

    def to_yaml(self, data: typing.Any) -> str:
        if isinstance(data, Sequence):
            if len(data) != 3:
                raise YAMLSerializationError(
                    f"expected a sequence of 3 elements, got {len(data)} elements"
                )
            for item in data:
                if not isinstance(item, int):
                    raise YAMLSerializationError(
                        f"expected int, got '{item}' of type '{type(item).__name__}'"
                    )
                if item < 0:
                    raise YAMLSerializationError(
                        f"expected non-negative int, got {item}"
                    )
            return ".".join(str(segment) for segment in data)
        if is_string(data):
            # we just validated that it's a string
            version_string = typing.cast(str, data)
            if self.REGEX.fullmatch(version_string) is None:
                raise YAMLSerializationError(
                    "expected Python version (MAJOR.MINOR.MICRO),"
                    f" got '{version_string}'"
                )
            return version_string
        raise YAMLSerializationError(
            "expected string or sequence,"
            f" got '{data}' of type '{type(data).__name__}'"
        )


# TODO: allow author in COG_KEYS and merge them with repo/shared fields lists
REPO_KEYS = {
    "name": Str(),  # Downloader doesn't use this but I can set friendlier name
    "short": Str(),
    "description": Str(),
    "install_msg": Str(),
    "author": Seq(Str()),
}
COMMON_KEYS = {
    Optional("min_bot_version"): Regex(VersionInfo._VERSION_STR_PATTERN),
    Optional("max_bot_version"): Regex(VersionInfo._VERSION_STR_PATTERN),
    Optional("min_python_version"): PythonVersion(),
    Optional("hidden", False): Bool(),
    Optional("disabled", False): Bool(),
    Optional("type", "COG"): Enum(["COG", "SHARED_LIBRARY"]),
}
SHARED_FIELDS_KEYS = {
    "install_msg": Str(),
    "author": Seq(Str()),
    **COMMON_KEYS,
}
COG_KEYS = {
    "name": Str(),  # Downloader doesn't use this but I can set friendlier name
    "short": Str(),
    "description": Str(),
    Optional("install_msg"): Str(),
    Optional("required_cogs", {}): EmptyDict() | MapPattern(Str(), Url()),
    Optional("requirements", []): EmptyList() | Seq(Str()),
    Optional("tags", []): EmptyList() | Seq(Str()),
    **COMMON_KEYS,
}
SCHEMA = Map(
    {
        "repo": Map(REPO_KEYS),
        "shared_fields": Map(SHARED_FIELDS_KEYS),
        "cogs": MapPattern(Str(), Map(COG_KEYS)),
    }
)
# TODO: auto-format to proper key order
AUTOLINT_REPO_KEYS_ORDER = list(REPO_KEYS.keys())
AUTOLINT_SHARED_FIELDS_KEYS_ORDER = list(
    getattr(key, "key", key) for key in SHARED_FIELDS_KEYS
)
AUTOLINT_COG_KEYS_ORDER = list(getattr(key, "key", key) for key in COG_KEYS)


def check_order(data: dict) -> int:
    """Temporary order checking, until strictyaml adds proper support for sorting."""
    to_check = {
        "repo": AUTOLINT_REPO_KEYS_ORDER,
        "shared_fields": AUTOLINT_SHARED_FIELDS_KEYS_ORDER,
    }
    exit_code = 0
    for key, order in to_check.items():
        section = data[key]
        original_keys = list(section.keys())
        sorted_keys = sorted(section.keys(), key=order.index)
        if original_keys != sorted_keys:
            print(
                "\033[93m\033[1mWARNING: \033[0m"
                f"Keys in `{key}` section have wrong order - use this order: "
                f"{', '.join(sorted_keys)}"
            )
            exit_code = 1

    original_cog_names = list(data["cogs"].keys())
    sorted_cog_names = sorted(data["cogs"].keys())
    if original_cog_names != sorted_cog_names:
        print(
            "\033[93m\033[1mWARNING: \033[0m"
            f"Cog names in `cogs` section aren't sorted. Use alphabetical order."
        )
        exit_code = 1

    for pkg_name, cog_info in data["cogs"].items():
        # strictyaml breaks ordering of keys for some reason
        original_keys = list((k for k, v in cog_info.items() if v))
        sorted_keys = sorted(
            (k for k, v in cog_info.items() if v), key=AUTOLINT_COG_KEYS_ORDER.index
        )
        if original_keys != sorted_keys:
            print(
                "\033[93m\033[1mWARNING: \033[0m"
                f"Keys in `cogs->{pkg_name}` section have wrong order"
                f" - use this order: {', '.join(sorted_keys)}"
            )
            print(original_keys)
            print(sorted_keys)
            exit_code = 1
        for key in ("required_cogs", "requirements", "tags"):
            list_or_dict = cog_info[key]
            if hasattr(list_or_dict, "keys"):
                original_list = list(list_or_dict.keys())
            else:
                original_list = list_or_dict
            sorted_list = sorted(original_list)
            if original_list != sorted_list:
                friendly_name = key.capitalize().replace("_", " ")
                print(
                    "\033[93m\033[1mWARNING: \033[0m"
                    f"{friendly_name} for `{pkg_name}` cog aren't sorted."
                    " Use alphabetical order."
                )
                print(original_list)
                print(sorted_list)
                exit_code = 1

    return exit_code


def main() -> int:
    print("Loading info.yaml...")
    with open(ROOT_PATH / "info.yaml", encoding="utf-8") as fp:
        data = yaml_load(fp.read(), SCHEMA).data

    print("Checking order in sections...")
    exit_code = check_order(data)

    print("Preparing repo's info.json...")
    repo_info = data["repo"]
    repo_info["install_msg"] = repo_info["install_msg"].format_map(
        {"repo_name": repo_info["name"]}
    )
    with open(ROOT_PATH / "info.json", "w", encoding="utf-8") as fp:
        json.dump(repo_info, fp, indent=4)

    requirements: typing.Set[str] = set()
    print("Preparing info.json files for cogs...")
    shared_fields = data["shared_fields"]
    cogs = data["cogs"]
    for pkg_name, cog_info in cogs.items():
        requirements.update(cog_info["requirements"])
        print(f"Preparing info.json for {pkg_name} cog...")
        output = {}
        for key in AUTOLINT_COG_KEYS_ORDER:
            value = cog_info.get(key)
            if value is None:
                value = shared_fields.get(key)
                if value is None:
                    continue
            output[key] = value
        replacements = {
            "repo_name": repo_info["name"],
            "cog_name": output["name"],
        }
        for to_replace in ("short", "description", "install_msg"):
            output[to_replace] = output[to_replace].format_map(replacements)

        with open(ROOT_PATH / pkg_name / "info.json", "w", encoding="utf-8") as fp:
            json.dump(output, fp, indent=4)

    print("Preparing requirements file for CI...")
    with open(ROOT_PATH / ".ci/requirements/all_cogs.txt", "w", encoding="utf-8") as fp:
        fp.write("Red-DiscordBot\n")
        for requirement in sorted(requirements):
            fp.write(f"{requirement}\n")

    print("Preparing all cogs list in README.md...")
    with open(ROOT_PATH / "README.md", "r+", encoding="utf-8") as fp:
        text = fp.read()
        match = re.search(
            r"# Cogs in this repo\n{2}(.+)\n{2}# Installation", text, flags=re.DOTALL
        )
        if match is None:
            print(
                "\033[91m\033[1mERROR:\033[0m Couldn't find cogs sections in README.md!"
            )
            return 1
        start, end = match.span(1)
        lines = []
        for pkg_name, cog_info in cogs.items():
            replacements = {
                "repo_name": repo_info["name"],
                "cog_name": cog_info["name"],
            }
            desc = cog_info["short"].format_map(replacements)
            lines.append(f"* **{pkg_name}** - {desc}")
        cogs_section = "\n".join(lines)
        fp.seek(0)
        fp.truncate()
        fp.write(f"{text[:start]}{cogs_section}{text[end:]}")

    print("Done!")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())