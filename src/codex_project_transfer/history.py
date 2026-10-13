"""Validate paginated ancestry and rebuild byte bounds after serialization."""
import copy
import json

from .core import TransferError, serialize, validate_id


def reference(rows):
    meta = rows[0]["payload"]
    base = meta.get("history_base")
    if base is None:
        return None
    if meta.get("history_mode") != "paginated" or not isinstance(base, dict):
        raise TransferError(f"{meta['id']}: unsupported history_base")
    parent = validate_id(base.get("thread_id"))
    cutoff = base.get("end_ordinal_exclusive")
    offset = base.get("end_byte_offset")
    if type(cutoff) is not int or cutoff <= 0 or type(offset) is not int or offset < 0:
        raise TransferError(f"{meta['id']}: invalid history_base bounds")
    return parent, cutoff


def prefix(rows, cutoff):
    if any(type(r.get("ordinal")) is not int for r in rows):
        raise TransferError("A paginated history dependency has no valid ordinals")
    if cutoff > rows[-1]["ordinal"] + 1 or cutoff <= rows[0]["ordinal"]:
        raise TransferError(f"{rows[0]['payload']['id']}: history dependency is truncated or has an invalid cutoff {cutoff}")
    return [r for r in rows if r["ordinal"] < cutoff]


def ordered(bundle):
    """Return parents before children; reject missing, cyclic or truncated graphs."""
    done, visiting, result = set(), set(), []

    def visit(sid):
        if sid in visiting:
            raise TransferError(f"Cyclic history dependency at {sid}")
        if sid in done:
            return
        if len(visiting) >= 128:
            raise TransferError("History dependency depth exceeds 128")
        visiting.add(sid)
        ref = reference(bundle[sid])
        if ref:
            parent, cutoff = ref
            if parent not in bundle:
                raise TransferError(f"{sid}: missing history dependency {parent}; re-export on the source device with its parent history")
            visit(parent)
            prefix(bundle[parent], cutoff)
        visiting.remove(sid)
        done.add(sid)
        result.append(sid)

    for sid in sorted(bundle):
        visit(sid)
    return result


def byte_offset(data, cutoff):
    """Measure the actual parent bytes, including its existing JSON formatting."""
    offset = 0
    rows = []
    for line in data.splitlines(keepends=True):
        if not line.strip():
            offset += len(line)
            continue
        row = json.loads(line)
        rows.append(row)
        ordinal = row.get("ordinal")
        if type(ordinal) is not int:
            raise TransferError("A paginated history dependency has no valid ordinals")
        if ordinal >= cutoff:
            break
        offset += len(line)
    prefix(rows, cutoff)
    return offset


def rebase(bundle):
    result = copy.deepcopy(bundle)
    encoded = {}
    for sid in ordered(result):
        ref = reference(result[sid])
        if ref:
            parent, cutoff = ref
            result[sid][0]["payload"]["history_base"]["end_byte_offset"] = byte_offset(encoded[parent], cutoff)
        encoded[sid] = serialize(result[sid])
    return result
