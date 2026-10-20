package play

import "fmt"

// ColoredNameBytes is the raw high-bit name MVDSV receives (/\xa0 + name|0x80).
func ColoredNameBytes(name string) string {
	raw := []byte{'/', 0xa0}
	for _, r := range name {
		raw = append(raw, byte(r)|0x80)
	}
	return string(raw)
}

// BotNameBinarySettings mirrors ktx_bot_name_binary_settings.
func BotNameBinarySettings(o Options, mode *Mode) ([][2]string, error) {
	settings, err := BotNameSettings(o, mode)
	if err != nil || len(settings) == 0 {
		return nil, err
	}
	count := RequestedNames(o, mode)
	var binary [][2]string
	for _, prefix := range botNamePrefixes {
		for index := 0; index < count; index++ {
			binary = append(binary, [2]string{fmt.Sprintf("%s_%d", prefix, index), ColoredNameBytes(o.BotNamePool[index])})
		}
	}
	return binary, nil
}
