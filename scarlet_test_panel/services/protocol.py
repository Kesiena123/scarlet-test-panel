"""Industrial dashboard message protocol.

Firmware <-> dashboard JSON protocol with Fletcher-16 integrity checking.

Outbound firmware messages look like:
    {"type":"data","counter":123,"direction":"up","feet":1.571,
     "uptime_s":5,"heartbeat":true,"crc":41622}

The trailing "crc":NNNN is a Fletcher-16 checksum computed over the bytes of
the payload that precedes the crc field. Any message whose checksum does not
match is rejected and logged (never displayed).
"""
import json

__all__ = ["fletcher16", "validate_message", "decode_message",
           "MessageError", "BadChecksum"]


class MessageError(Exception):
    pass


class BadChecksum(MessageError):
    pass


def fletcher16(data: bytes) -> int:
    """Fletcher-16 checksum, byte-for-byte compatible with the firmware."""
    s1 = 0
    s2 = 0
    for b in data:
        s1 = (s1 + b) % 255
        s2 = (s2 + s1) % 255
    return (s2 << 8) | s1


def validate_message(raw: str):
    """Verify the Fletcher-16 checksum on a raw line.

    Returns the parsed JSON dict on success.
    Raises BadChecksum if the checksum is missing or does not match.
    Raises json.JSONDecodeError / MessageError for malformed messages.
    """
    line = (raw or "").strip()
    if not line:
        raise MessageError("empty message")

    idx = line.rfind(',"crc":')
    if idx < 0:
        raise BadChecksum("message has no crc field")

    payload = line[1:idx]                 # strip leading '{' and crc segment
    try:
        recv_crc = int(line[idx + len(',"crc":'):-1])
        data = json.loads(line)
    except (ValueError, json.JSONDecodeError) as exc:
        raise BadChecksum(f"malformed crc/JSON: {exc}")

    calc_crc = fletcher16(payload.encode('ascii'))
    if calc_crc != recv_crc:
        raise BadChecksum(f"crc mismatch (recv={recv_crc}, calc={calc_crc})")
    return data


def decode_message(raw: str):
    """Alias returning the parsed dict after checksum validation."""
    return validate_message(raw)
