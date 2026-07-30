"""Reads and writes the model catalog file, and answers questions about individual models.

The model catalog (``model_catalog.json``, in the folder this person keeps
their own files in — see ``src/paths.py``) is the single source of truth for
which AI models are available, what they cost per token, and what special
capabilities or limitations each one has. Functions in this module load that
file, look up pricing and properties for a given model, and save any changes
back to disk.
"""

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

MODEL_CATALOG_FILE = "model_catalog.json"

# The parameters that shape how varied a model's wording is. Grouped because
# providers treat them as a set: a model that refuses one generally refuses all
# of them.
_SAMPLING_FIELDS: tuple = ("temperature", "top_p", "frequency_penalty", "presence_penalty")

# Two ways of writing the same thing. A model's quirks belong under ``rejects``
# (request fields it won't accept) and ``prefers`` (fields needing a value
# other than the usual one), which is what the sandbox learns into and what the
# catalogue template ships. A catalogue may instead spell some of them as
# individual flags, which are read and understood as the equivalent entry —
# the two below map a flag onto every field it stands for.
_FLAGS_MEANING_REJECTED: dict = {
    "fixed_parameters": _SAMPLING_FIELDS,
    "omit_sampling_params": _SAMPLING_FIELDS,
}

# In-memory cache: populated on first load, invalidated whenever the catalog
# is written.  Eliminates repeated file-descriptor opens during parallel
# translation where each worker would otherwise call load_model_catalog() for
# every API call.
#
# The cache is keyed on the resolved file path plus a "has this file changed?"
# stamp, so that:
#   a) test fixtures that redirect get_model_catalog_path() to a tmp file get
#      a fresh read automatically.
#   b) any writer (including test helpers that bypass save_model_catalog) that
#      modifies the file on disk invalidates the cache.
#
# The stamp is (modification time in nanoseconds, size in bytes), not the plain
# whole-second-ish mtime this used to use. Two writes to the same file in quick
# succession can land in the same mtime tick — some filesystems only record the
# time to the nearest second, and even high-resolution ones can report the same
# float for two writes microseconds apart. When that happened, the file had
# changed but the stamp hadn't, so this kept serving the previous contents:
# a professor who hand-edited model_catalog.json while the web interface was
# running would be told their newly added model doesn't exist. Nanosecond
# precision plus the file size makes an unnoticed change far less likely.
_catalog_cache: Optional[Dict[str, Any]] = None
_catalog_cache_path: Optional[Path] = None
_catalog_cache_stamp: Optional[tuple[int, int]] = None


def get_model_catalog_path() -> Path:
    """Return the model catalogue file, wherever this installation keeps it."""
    from ..paths import model_catalog_path
    return model_catalog_path()


def _file_change_stamp(path: Path) -> Optional[tuple[int, int]]:
    """Return a small value that changes whenever *path*'s contents change.

    Used to decide whether the cached catalog is still current. The value is
    the file's last-modified time (in nanoseconds) paired with its size in
    bytes; comparing it against the previously recorded value answers "has
    this file been rewritten since we last read it?" without re-reading and
    re-parsing the whole file.

    Args:
        path: The file to inspect.

    Returns:
        A ``(modified_time_ns, size_in_bytes)`` pair, or ``None`` if the file
        can't be inspected at all (most often because it doesn't exist yet).
    """
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def load_model_catalog() -> Dict[str, Any]:
    """Read the model catalog file and return its contents.

    To avoid re-opening the file on every API call during a long translation
    job, the result is kept in memory after the first read and reused for the
    rest of the session (caching). The remembered copy is discarded and the
    file re-read whenever its modification time or size has changed since the
    last read, so hand-edits to ``model_catalog.json`` take effect on the next
    call — including while a long-running process such as the web interface is
    already up.

    Returns:
        A dictionary with two top-level keys: ``'config'`` (global settings
        such as pricing unit and monthly limit) and ``'models'`` (per-model
        pricing and capability flags).

    Raises:
        FileNotFoundError: If ``model_catalog.json`` does not exist.
            The error message explains how to create it from the template.
        ValueError: If the file contains invalid JSON or is missing required
            sections.
    """
    global _catalog_cache, _catalog_cache_path, _catalog_cache_stamp

    catalog_file = get_model_catalog_path()
    current_stamp = _file_change_stamp(catalog_file)

    if (
        _catalog_cache is not None
        and _catalog_cache_path == catalog_file
        and _catalog_cache_stamp == current_stamp
    ):
        return _catalog_cache


    if not catalog_file.exists():
        # The template ships with the package; the catalog belongs to the
        # person and lives wherever their extras folder is, so the two are
        # no longer siblings and the path can't be derived from the other.
        from ..paths import template_path
        template_file = template_path("model_catalog.template.json")
        error_msg = (
            f"No model catalogue found at {catalog_file}.\n"
            "Setup creates this for you — run it once:\n"
            "  python main.py settings setup\n"
            f"(The starting point it copies from is {template_file}, if you "
            "would rather put it there yourself.)"
        )
        logging.error(error_msg)
        raise FileNotFoundError(error_msg)

    try:
        with open(catalog_file, "r") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        error_msg = f"Invalid JSON in model catalog file {catalog_file}: {e}"
        logging.error(error_msg)
        raise ValueError(error_msg) from e

    if "config" not in config:
        error_msg = f"Model catalog file {catalog_file} missing required 'config' section."
        logging.error(error_msg)
        raise ValueError(error_msg)

    if "models" not in config:
        error_msg = f"Model catalog file {catalog_file} missing required 'models' section."
        logging.error(error_msg)
        raise ValueError(error_msg)

    if not config["models"]:
        error_msg = f"Model catalog file {catalog_file} has no models configured."
        logging.error(error_msg)
        raise ValueError(error_msg)


    # Deliberately records the stamp taken *before* the file was read, not a
    # fresh one. If the file changed while we were reading it, the stamp we
    # store is already out of date, so the next call re-reads — one wasted
    # read, which is the harmless direction. Re-stamping here would instead
    # pair the new stamp with possibly-older contents and cache them.
    _catalog_cache = config
    _catalog_cache_path = catalog_file
    _catalog_cache_stamp = current_stamp
    return config


def save_model_catalog(config: Dict[str, Any]) -> None:
    """Write an updated model catalog to disk and clear the in-memory cache.

    The file is written completely to a temporary location before replacing
    the live catalog, so a crash or power loss mid-write can never leave a
    partially written file behind (atomic write). The in-memory cache is
    cleared so the next read picks up the new contents.

    Args:
        config: The complete catalog dictionary to save, in the same structure
                returned by ``load_model_catalog()`` — a dict with ``'config'``
                and ``'models'`` keys.
    """
    global _catalog_cache
    _catalog_cache = None  # Invalidate before write so any concurrent reader re-reads
    catalog_file = get_model_catalog_path()
    catalog_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=catalog_file.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp_path, catalog_file)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_available_models() -> List[str]:
    """Get available models from the model catalog."""
    config = load_model_catalog()
    return list(config["models"].keys())


def get_model_pricing(model: str) -> Dict[str, float]:
    """Return the input and output cost rates for a model from the catalog.

    If the requested model is not in the catalog, falls back to
    ``gpt-4o-mini`` pricing and logs a warning. This prevents a missing entry
    from halting a job, though the cost estimate will be approximate.

    Args:
        model: The model name exactly as it appears in ``model_catalog.json``
               (e.g. ``'gpt-4o'``, ``'gpt-4o-mini'``).

    Returns:
        A dictionary with at minimum ``'input'`` and ``'output'`` keys whose
        values are the cost per pricing unit (see ``get_pricing_unit()``).

    Raises:
        ValueError: If the model is not in the catalog and no other model there
                    carries a price that could stand in for it.
    """
    config = load_model_catalog()
    models = config["models"]

    if model not in models:
        # Priced against the cheapest model rather than a named stand-in, so
        # this keeps working when whichever model used to be named here is
        # retired. It is a guess either way — the point is only that a call
        # already made gets recorded rather than lost.
        stand_in = cheapest_model()
        if stand_in is not None:
            logging.warning(
                f"Model {model} is not in the catalog, so its cost cannot be worked out "
                f"exactly. Recording it at {stand_in}'s rates instead; add {model} to "
                "model_catalog.json for accurate figures."
            )
            return models[stand_in]
        available_models = list(models.keys())
        error_msg = (
            f"Model '{model}' is not in the model catalog, and no other model there has "
            f"a price to stand in for it. "
            f"Available models: {available_models}. "
            "Please update your model catalog file."
        )
        logging.error(error_msg)
        raise ValueError(error_msg)

    return models[model]


def get_pricing_unit() -> int:
    """Get the pricing unit from the model catalog config."""
    config = load_model_catalog()
    return config["config"]["pricing_unit"]


def get_monthly_limit() -> float:
    """Get the monthly spending limit from the model catalog config."""
    config = load_model_catalog()
    return config["config"]["monthly_limit"]


def model_supports_vision(model: str) -> bool:
    """Check whether a model can accept images as part of a request.

    Models that support vision can be used for OCR and image translation.
    The capability is controlled by the ``supports_vision`` flag in
    ``model_catalog.json``. Returns ``False`` for any model not in the
    catalog.

    Args:
        model: The model name to check (e.g. ``'gpt-4o'``).

    Returns:
        ``True`` if the model's catalog entry has ``"supports_vision": true``,
        ``False`` otherwise.
    """
    config = load_model_catalog()
    models = config["models"]

    if model not in models:
        logging.warning(f"Model {model} not found in pricing config. Assuming no vision support.")
        return False

    return models[model].get("supports_vision", False)


def get_vision_capable_models() -> List[str]:
    """Get list of models that support vision/image processing."""
    config = load_model_catalog()
    return [
        model for model, details in config["models"].items()
        if details.get("supports_vision", False)
    ]


def get_model_system_role(model: str) -> str:
    """Return the role label the model expects for system-level instructions.

    Most models accept a ``"system"`` role for the instruction message at the
    start of a conversation. Some newer reasoning models require the label
    ``"developer"`` instead and reject requests that use ``"system"``. This
    looks the answer up so each model receives the form it expects.

    Args:
        model: The model name to look up (e.g. ``'gpt-4o'``, ``'gpt-5'``).

    Returns:
        ``'system'`` for most models, or whatever the model's ``prefers``
        entry names — ``'developer'`` in practice. ``'system'`` for a model
        that isn't in the catalog.
    """
    role = model_preferences(model).get("system_role")
    return role if isinstance(role, str) and role else "system"


def model_max_tokens_field(model: str) -> str:
    """Return the name this model wants for the response-length cap.

    Most models call it ``max_tokens``. Some reasoning models reject that name
    and require ``max_completion_tokens`` instead — the same setting, spelled
    differently. Returning the name rather than a yes-or-no answer keeps the
    caller from having to know which two names the question is between.

    Not to be confused with ``get_model_max_completion_tokens()``, which
    answers how *long* the response may be. This one answers what to call it.

    Args:
        model: The model name to look up (e.g. ``'gpt-4o'``, ``'gpt-5'``).

    Returns:
        ``'max_tokens'`` or ``'max_completion_tokens'``.
    """
    preferred = model_preferences(model).get("max_tokens_field")
    return "max_completion_tokens" if preferred == "max_completion_tokens" else "max_tokens"


def model_accepts_sampling_params(model: str) -> bool:
    """Check whether a model will accept being told how varied its wording should be.

    Most models take ``temperature`` and ``top_p``. Some reasoning models
    reject them outright, and some provider routes reject them even where the
    model itself would not. Either way the answer is the same — leave them out
    — so there is one question here rather than two nearly-identical ones.

    Args:
        model: The model name to check (e.g. ``'gpt-4o'``).

    Returns:
        ``False`` if the model is known to refuse any of the sampling
        parameters, ``True`` otherwise (including for a model that isn't in
        the catalog, which is the optimistic default: a refusal teaches the
        catalog the answer, see ``record_rejected_field()``).
    """
    rejected = model_rejected_fields(model)
    return not any(field in rejected for field in _SAMPLING_FIELDS)


def _with_quirks_gathered(entry: Any) -> Any:
    """Return one model's catalog entry with its quirks gathered under two keys.

    A model's awkwardnesses are described by ``rejects`` (request fields it
    won't accept) and ``prefers`` (fields that need a value other than the
    usual one). A catalog may instead spell some of them as individual flags —
    ``fixed_parameters``, ``omit_sampling_params``, ``use_max_completion_tokens``
    and ``system_role``. Both are understood; this turns the second into the
    first so that nothing further down has to check two places.

    Args:
        entry: One value from the catalog's ``models`` section. Anything that
               isn't a dictionary is handed back untouched — a hand-edited file
               can contain surprises, and this is not the place to complain
               about them.

    Returns:
        An entry whose quirks are all under ``rejects`` and ``prefers``, with
        the individual flags removed. Where both spellings are present the
        ``rejects``/``prefers`` entry wins, since that is the one the sandbox
        learns into and therefore the more recent answer.
    """
    if not isinstance(entry, dict):
        return entry

    raw_rejects = entry.get("rejects")
    raw_prefers = entry.get("prefers")
    rejects: dict = dict(raw_rejects) if isinstance(raw_rejects, dict) else {}
    prefers: dict = dict(raw_prefers) if isinstance(raw_prefers, dict) else {}
    flags_seen = False

    for flag, fields in _FLAGS_MEANING_REJECTED.items():
        if entry.get(flag):
            flags_seen = True
            for field in fields:
                rejects.setdefault(field, f"catalog: {flag}")

    if entry.get("use_max_completion_tokens"):
        flags_seen = True
        prefers.setdefault("max_tokens_field", "max_completion_tokens")

    role = entry.get("system_role")
    if isinstance(role, str) and role:
        flags_seen = True
        prefers.setdefault("system_role", role)

    if not flags_seen:
        return entry

    gathered = {
        key: value for key, value in entry.items()
        if key not in _FLAGS_MEANING_REJECTED
        and key not in ("use_max_completion_tokens", "system_role")
    }
    if rejects:
        gathered["rejects"] = rejects
    if prefers:
        gathered["prefers"] = prefers
    return gathered


def model_preferences(model: str) -> dict[str, Any]:
    """Return the values this model needs in place of the usual ones.

    Where ``model_rejected_fields()`` lists what a model won't accept at all,
    this lists what it accepts only in a particular form — which name it wants
    for the response-length cap, what to call the system message's role. The
    distinction matters: those two were never yes-or-no questions, so recording
    them as flags loses the answer.

    Args:
        model: The model name to look up (e.g. ``'gpt-5'``).

    Returns:
        A dictionary of setting name to value, empty for a model with no
        special needs. Recognised keys are ``'max_tokens_field'`` and
        ``'system_role'``.
    """
    entry = _with_quirks_gathered(load_model_catalog()["models"].get(model, {}))
    preferred = entry.get("prefers") if isinstance(entry, dict) else None
    return dict(preferred) if isinstance(preferred, dict) else {}


def model_rejected_fields(model: str) -> dict[str, str]:
    """Return the request fields this model has told us it will not accept.

    Providers differ over which optional pieces of a request they allow, and
    there is no way to know in advance — one route accepts a field another
    refuses outright. Rather than keep a hand-written list of every quirk,
    the sandbox learns each one the first time a provider objects and records
    it here, so later requests leave that field out from the start.

    Args:
        model: The model name to check (e.g. ``'mistral-small-2503'``).

    Returns:
        A dictionary of field name to the note recorded when it was learned
        (e.g. ``{'stream_options': 'azure-ai, 2026-07-29: Extra inputs are
        not permitted'}``). Empty for a model with nothing known against it,
        which is the normal case.
    """
    entry = _with_quirks_gathered(load_model_catalog()["models"].get(model, {}))
    rejected = entry.get("rejects") if isinstance(entry, dict) else None
    return dict(rejected) if isinstance(rejected, dict) else {}


def record_rejected_field(model_name: str, field: str, reason: str) -> bool:
    """Remember that a model refuses one of the optional fields in a request.

    Called when a provider replies that a particular field is not allowed —
    see ``rejected_request_field()`` in ``src/services/api_errors.py``, which
    reads the field name out of the error. Once recorded, every later request
    for this model leaves that field out (see
    ``BaseService._build_completion_kwargs()``), so the same refusal doesn't
    happen twice.

    The reason is stored alongside the field, dated, so that anyone reading
    ``model_catalog.json`` later can tell what was learned automatically from
    what was set by hand, and judge whether it is still true. Nothing expires
    on its own: if a provider starts accepting the field again, delete the
    entry to have it learned afresh.

    Args:
        model_name: The model's catalog key (e.g. ``'mistral-small-2503'``).
        field: The request field the provider refused (e.g.
               ``'stream_options'``).
        reason: A short description of what the provider said, for the record.

    Returns:
        ``True`` if this is newly recorded, ``False`` if the model isn't in
        the catalog or already had this field recorded (no changes are made
        in either case).
    """
    catalog = load_model_catalog()
    entry = catalog["models"].get(model_name)
    if entry is None:
        return False
    rejected = entry.get("rejects")
    if not isinstance(rejected, dict):
        rejected = {}
    if field in rejected:
        return False
    rejected[field] = f"{datetime.now().strftime('%Y-%m-%d')}: {reason}"
    entry["rejects"] = rejected
    save_model_catalog(catalog)
    logging.warning(
        f"Model '{model_name}' does not accept '{field}' — recorded in the catalog "
        f"so later requests leave it out. Reason given: {reason}"
    )
    return True


def get_model_max_completion_tokens(model: str, default: int) -> int:
    """Return the response-length cap to use for a model, applying any per-model override from the catalog.

    Some models (particularly reasoning models that do internal thinking before
    responding) consume part of the token budget on steps that are not visible
    in the final response. Those models therefore need a larger cap than
    standard models. Setting ``max_completion_tokens`` in the catalog entry
    for such a model overrides the value that would otherwise be used.

    Args:
        model: The model name to look up (e.g. ``'gpt-4o'``).
        default: The cap to use if no per-model override is set in the catalog
                 (e.g. ``4096``).

    Returns:
        The per-model override from the catalog if present, otherwise
        ``default``.
    """
    config = load_model_catalog()
    return config["models"].get(model, {}).get("max_completion_tokens", default)




def cheapest_model(require_vision: bool = False) -> Optional[str]:
    """Return the least expensive model in the catalog that can do the job.

    The last resort when nothing a role asked for is left. Cost is the sum of
    what a model charges to read and to write one pricing unit of text (see
    ``get_pricing_unit()``) — a rough ranking rather than a real forecast,
    since the balance of reading to writing differs by task, but enough to
    avoid landing on the most expensive model by accident.

    Args:
        require_vision: Whether the model has to be able to read images.

    Returns:
        The cheapest qualifying model's name, or ``None`` if none qualifies.

    Notes:
        A model whose input and output prices are both zero is treated as
        having no known price and skipped, not as free: that pairing is what an
        unpriced placeholder looks like (the shipped catalog template contains
        one), and letting it win this comparison would quietly make an
        unpriced model the default for everything.
    """
    priced: list[tuple[float, str]] = []
    for name, entry in load_model_catalog()["models"].items():
        if require_vision and not entry.get("supports_vision", False):
            continue
        read, write = entry.get("input"), entry.get("output")
        if not isinstance(read, (int, float)) or not isinstance(write, (int, float)):
            continue
        if read == 0 and write == 0:
            continue
        priced.append((read + write, name))
    if not priced:
        return None
    # Sorted on the name as well as the price so that two equally-priced models
    # always resolve the same way, rather than by whatever order the file
    # happened to be written in.
    return min(priced, key=lambda pair: (pair[0], pair[1]))[1]


def remove_model_from_catalog(model_name: str) -> bool:
    """Delete a model entry from the catalog and save the updated file to disk.

    Used automatically when the system detects that a model is no longer
    accessible through the AI gateway. Logs a warning when the model is
    removed so the change is visible in the run log.

    Args:
        model_name: The exact key of the model to remove, as it appears in
                    ``model_catalog.json`` (e.g. ``'gpt-4o-2024-08-06'``).

    Returns:
        ``True`` if the model was found and removed, ``False`` if the model
        was not present in the catalog (no changes are made in that case).
    """
    catalog = load_model_catalog()
    if model_name not in catalog["models"]:
        return False
    del catalog["models"][model_name]
    save_model_catalog(catalog)
    logging.warning(f"Removed inaccessible model '{model_name}' from catalog.")
    return True


def is_model_access_error(error_message: str) -> bool:
    """Return True if the error message indicates the model is not accessible
    in the Princeton AI Sandbox (PortKey router cannot find it).
    """
    return "invalid target name found in the query router" in error_message.lower()


def record_sampling_params_rejected(model_name: str) -> bool:
    """Remember that a model won't accept being told how varied its wording should be.

    Called when a provider reports that temperature, top-p and the rest are not
    merely out of range but not accepted at all — see
    ``is_sampling_param_deprecated_error()``. Recorded the same way as any
    other refused field, so there is one list of a model's awkwardnesses rather
    than a separate flag for this particular one.

    Args:
        model_name: The model's catalog key (e.g. ``'gpt-5'``).

    Returns:
        ``True`` if anything was newly recorded, ``False`` if the model isn't
        in the catalog or already refused all of them.
    """
    recorded = False
    for field in _SAMPLING_FIELDS:
        if record_rejected_field(model_name, field, "provider reports it is not accepted"):
            recorded = True
    return recorded


def is_sampling_param_deprecated_error(error_message: str) -> bool:
    """Return True if the error message indicates a model has dropped support
    for temperature/top-p/etc. entirely, rather than merely rejecting an
    out-of-range value.

    Matches the Azure AI gateway's ``` `temperature` is deprecated for this
    model.``` (and the equivalent for other sampling parameters) rather than
    a generic invalid-request error, so this only fires for the specific
    "this model no longer accepts this parameter at all" case.
    """
    msg = error_message.lower()
    if "deprecated for this model" not in msg:
        return False
    return any(
        param in msg for param in ("temperature", "top_p", "top-p", "frequency_penalty", "presence_penalty")
    )
