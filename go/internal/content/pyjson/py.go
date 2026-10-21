package pyjson

import (
	"math/big"
	"strconv"
)

// Predicates the validators rely on: numbers compare by value, and a bool
// counts as 0 or 1.

// IsStr reports isinstance(value, str).
func IsStr(value any) bool {
	_, ok := value.(string)
	return ok
}

// NonEmptyStr reports isinstance(value, str) and value.
func NonEmptyStr(value any) bool {
	text, ok := value.(string)
	return ok && text != ""
}

// IsInstanceInt reports isinstance(value, int), which includes bool.
func IsInstanceInt(value any) bool {
	switch typed := value.(type) {
	case bool:
		return true
	case Number:
		return typed.IsInt()
	case int, int64:
		return true
	}
	return false
}

// IsExactInt reports type(value) is int.
func IsExactInt(value any) bool {
	switch typed := value.(type) {
	case Number:
		return typed.IsInt()
	case int, int64:
		return true
	}
	return false
}

// IsNumber reports isinstance(value, (int, float)) including bool.
func IsNumber(value any) bool {
	switch value.(type) {
	case bool, Number, int, int64:
		return true
	}
	return false
}

func rat(value any) *big.Rat {
	switch typed := value.(type) {
	case bool:
		if typed {
			return big.NewRat(1, 1)
		}
		return new(big.Rat)
	case Number:
		return typed.Rat()
	case int:
		return big.NewRat(int64(typed), 1)
	case int64:
		return big.NewRat(typed, 1)
	}
	return nil
}

// Compare compares two numeric values (a bool counts as an integer).
// ok is false when either side is not numeric or is NaN/Infinity.
func Compare(a, b any) (int, bool) {
	left, right := rat(a), rat(b)
	if left == nil || right == nil {
		return 0, false
	}
	return left.Cmp(right), true
}

// IntCmp compares a numeric value with an integer constant.
func IntCmp(value any, constant int64) (int, bool) {
	return Compare(value, Number(strconv.FormatInt(constant, 10)))
}

// Eq reports whether a and b are equal JSON values: numbers by value (true
// equals 1, NaN equals nothing), lists in order, objects key by key.
func Eq(a, b any) bool {
	if IsNumber(a) && IsNumber(b) {
		if n, ok := a.(Number); ok && n == "NaN" {
			return false
		}
		if n, ok := b.(Number); ok && n == "NaN" {
			return false
		}
		if na, ok := a.(Number); ok && na.special() {
			nb, ok := b.(Number)
			return ok && na == nb
		}
		if nb, ok := b.(Number); ok && nb.special() {
			return false
		}
		order, ok := Compare(a, b)
		return ok && order == 0
	}
	switch typed := a.(type) {
	case nil:
		return b == nil
	case string:
		other, ok := b.(string)
		return ok && typed == other
	case []any:
		other, ok := b.([]any)
		if !ok || len(other) != len(typed) {
			return false
		}
		for index := range typed {
			if !Eq(typed[index], other[index]) {
				return false
			}
		}
		return true
	case []string:
		items := make([]any, len(typed))
		for index, item := range typed {
			items[index] = item
		}
		return Eq(items, b)
	case *Object:
		other, ok := b.(*Object)
		if !ok || other.Len() != typed.Len() {
			return false
		}
		for _, key := range typed.keys {
			value, ok := other.Get(key)
			if !ok || !Eq(typed.values[key], value) {
				return false
			}
		}
		return true
	}
	if _, ok := b.([]string); ok {
		return Eq(b, a)
	}
	return false
}

// EqInt reports whether value equals the integer constant.
func EqInt(value any, constant int64) bool {
	return Eq(value, Number(strconv.FormatInt(constant, 10)))
}

// Truthy reports whether value is not null, false, zero or empty.
func Truthy(value any) bool {
	switch typed := value.(type) {
	case nil:
		return false
	case bool:
		return typed
	case Number:
		if typed == "NaN" {
			return true
		}
		order, ok := Compare(typed, Number("0"))
		return !ok || order != 0
	case string:
		return typed != ""
	case []any:
		return len(typed) > 0
	case *Object:
		return typed.Len() > 0
	}
	return true
}

// Repr renders a scalar as the text the validation patterns match; lists and
// objects never match them.
func Repr(value any) string {
	switch typed := value.(type) {
	case nil:
		return "None"
	case bool:
		if typed {
			return "True"
		}
		return "False"
	case Number:
		if typed.IsInt() {
			text, _ := numberText(typed)
			return text
		}
		switch typed {
		case "NaN":
			return "nan"
		case "Infinity":
			return "inf"
		case "-Infinity":
			return "-inf"
		}
		return FloatRepr(typed.Float())
	case string:
		return typed
	case []any:
		return "[…]"
	case *Object:
		return "{…}"
	}
	return ""
}

// StrList returns value as []string when it is a list of strings.
func StrList(value any) ([]string, bool) {
	items, ok := value.([]any)
	if !ok {
		return nil, false
	}
	result := make([]string, 0, len(items))
	for _, item := range items {
		text, ok := item.(string)
		if !ok {
			return nil, false
		}
		result = append(result, text)
	}
	return result, true
}

// Hashable reports whether value is a scalar, the only kind HashKey takes.
func Hashable(value any) bool {
	switch value.(type) {
	case []any, *Object:
		return false
	}
	return true
}

// HashKey returns a key under which equal scalars (1, 1.0 and true)
// collide.
func HashKey(value any) string {
	switch typed := value.(type) {
	case nil:
		return "n"
	case string:
		return "s" + typed
	case bool, Number, int, int64:
		if r := rat(typed); r != nil {
			return "#" + r.RatString()
		}
		return "#" + Repr(typed)
	}
	return "?"
}

// UniqueHashable reports whether values has no duplicates; ok is false when
// one is not a scalar.
func UniqueHashable(values []any) (unique bool, ok bool) {
	seen := map[string]bool{}
	for _, value := range values {
		if !Hashable(value) {
			return false, false
		}
		key := HashKey(value)
		if seen[key] {
			return false, true
		}
		seen[key] = true
	}
	return true, true
}

// Get walks nested objects; it returns nil when any step is missing.
func Get(value any, keys ...string) any {
	for _, key := range keys {
		object, ok := value.(*Object)
		if !ok {
			return nil
		}
		value = object.Value(key)
	}
	return value
}
