package play

import (
	"bytes"
	"errors"
)

// Payload renders the cfg exactly like write_ktx_runtime_config.
func Payload(settings [][2]string, startup []string) ([]byte, error) {
	var buffer bytes.Buffer
	buffer.WriteString("// x86QW: ephemeral KTX launch configuration\n")
	if len(settings) > 0 {
		for _, command := range BotNameCleanupCommands() {
			buffer.WriteString(command + "\n")
		}
	}
	for _, setting := range settings {
		buffer.WriteString("set_ex " + setting[0] + " $qt" + setting[1] + "$qt\n")
	}
	for _, command := range startup {
		if command == "" {
			return nil, errors.New("Configuração efêmera KTX contém comando inválido.")
		}
		for _, r := range command {
			if r < 32 || r > 127 {
				return nil, errors.New("Configuração efêmera KTX contém comando inválido.")
			}
		}
		buffer.WriteString(command + "\n")
	}
	return buffer.Bytes(), nil
}
