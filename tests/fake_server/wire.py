"""Strict raw-wire checks that protobuf's permissive parser doesn't perform."""

from google.protobuf.descriptor import Descriptor, FieldDescriptor


class InvalidWire(ValueError):
    """The bytes violate Bazaar's stricter-than-protobuf wire rules."""


_VARINT_TYPES = {
    FieldDescriptor.TYPE_BOOL,
    FieldDescriptor.TYPE_ENUM,
    FieldDescriptor.TYPE_INT32,
    FieldDescriptor.TYPE_INT64,
    FieldDescriptor.TYPE_SINT32,
    FieldDescriptor.TYPE_SINT64,
    FieldDescriptor.TYPE_UINT32,
    FieldDescriptor.TYPE_UINT64,
}
_FIXED_64_TYPES = {FieldDescriptor.TYPE_DOUBLE, FieldDescriptor.TYPE_FIXED64}
_LENGTH_TYPES = {
    FieldDescriptor.TYPE_BYTES,
    FieldDescriptor.TYPE_MESSAGE,
    FieldDescriptor.TYPE_STRING,
}
_FIXED_32_TYPES = {FieldDescriptor.TYPE_FIXED32, FieldDescriptor.TYPE_FLOAT}


def validate_wire(data: bytes, descriptor: Descriptor) -> None:
    """Reject unknown, duplicate singular, and unknown-enum fields recursively."""
    position = 0
    seen_fields: set[int] = set()
    seen_oneofs: set[str] = set()

    while position < len(data):
        tag, position = _read_varint(data, position)
        field_number = tag >> 3
        wire_type = tag & 7
        field = descriptor.fields_by_number.get(field_number)
        if field is None or field_number == 0:
            raise InvalidWire("unknown field")
        if wire_type != _expected_wire_type(field):
            raise InvalidWire("wrong wire type")
        if not field.is_repeated and field_number in seen_fields:
            raise InvalidWire("duplicate singular field")
        seen_fields.add(field_number)

        if field.containing_oneof is not None:
            oneof_name = field.containing_oneof.full_name
            if oneof_name in seen_oneofs:
                raise InvalidWire("multiple values for oneof")
            seen_oneofs.add(oneof_name)

        if wire_type == 0:
            value, position = _read_varint(data, position)
            if field.type == FieldDescriptor.TYPE_ENUM:
                if value not in field.enum_type.values_by_number:
                    raise InvalidWire("unknown enum value")
        elif wire_type == 1:
            position = _advance(data, position, 8)
        elif wire_type == 2:
            length, position = _read_varint(data, position)
            end = _advance(data, position, length)
            if field.type == FieldDescriptor.TYPE_MESSAGE:
                validate_wire(data[position:end], field.message_type)
            position = end
        elif wire_type == 5:
            position = _advance(data, position, 4)
        else:
            raise InvalidWire("unsupported wire type")


def _expected_wire_type(field: FieldDescriptor) -> int:
    if field.type in _VARINT_TYPES:
        return 0
    if field.type in _FIXED_64_TYPES:
        return 1
    if field.type in _LENGTH_TYPES:
        return 2
    if field.type in _FIXED_32_TYPES:
        return 5
    raise InvalidWire("unsupported protobuf field type")


def _read_varint(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(data):
            raise InvalidWire("truncated varint")
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, position
    raise InvalidWire("varint is too long")


def _advance(data: bytes, position: int, amount: int) -> int:
    end = position + amount
    if end > len(data):
        raise InvalidWire("truncated field")
    return end
