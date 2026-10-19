package services

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"
	"time"
)

// Readiness probes ported from x86qw_runtime/supervisor/readiness.py.

// PortRequest is one listener a service will bind.
type PortRequest struct {
	Label, Address string
	Port           int
	Kind           string
}

// RequestedPorts mirrors services.requested_ports.
func RequestedPorts(action string, host *HostOptions, proxy *ProxyOptions, qtv *QtvOptions) []PortRequest {
	switch action {
	case "proxy":
		return []PortRequest{{"QWFWD", proxy.ProxyBind, proxy.ProxyPort, "udp"}}
	case "qtv":
		return []PortRequest{{"QTV", qtv.Bind, qtv.Port, "tcp"}}
	}
	requests := []PortRequest{{"MVDSV", host.Bind, host.Port, "udp"}}
	if host.WithQTV {
		requests = append(requests, PortRequest{"QTV", host.QtvBind, host.QtvPort, "tcp"})
	}
	if host.WithProxy {
		requests = append(requests, PortRequest{"QWFWD", host.ProxyBind, host.ProxyPort, "udp"})
	}
	return requests
}

// PreflightPorts mirrors readiness.preflight_ports.
func PreflightPorts(requests []PortRequest) error {
	seen := map[int]string{}
	for _, request := range requests {
		if previous, ok := seen[request.Port]; ok {
			return errorf("Porta local duplicada: %d foi solicitada por %s e %s.", request.Port, previous, request.Label)
		}
		seen[request.Port] = request.Label
		address := Endpoint(request.Address, request.Port)
		var closer io.Closer
		var err error
		if request.Kind == "tcp" {
			closer, err = net.Listen(network("tcp", request.Address), address)
		} else {
			closer, err = net.ListenPacket(network("udp", request.Address), address)
		}
		if err != nil {
			return errorf("A porta %s de %s não está disponível.", address, request.Label)
		}
		closer.Close()
	}
	return nil
}

func network(kind, address string) string {
	if strings.Contains(address, ":") {
		return kind + "6"
	}
	return kind + "4"
}

func qtvResponseReady(response []byte, upstream string) bool {
	statusLine, _, _ := bytes.Cut(response, []byte("\r\n"))
	fields := strings.Fields(string(statusLine))
	if len(fields) < 2 || !strings.HasPrefix(fields[0], "HTTP/") {
		return false
	}
	code, err := strconv.Atoi(fields[1])
	if err != nil || strings.ContainsAny(fields[1], "+-") || code < 200 || code >= 300 {
		return false
	}
	if upstream == "" {
		return true
	}
	_, body, found := bytes.Cut(response, []byte("\r\n\r\n"))
	return found && bytes.Contains(bytes.ToLower(body), []byte(strings.ToLower(upstream)))
}

func sleepContext(ctx context.Context, duration time.Duration) error {
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// waitHTTPReadiness mirrors wait_http_readiness (GET /nowplaying/ over 8 s).
func waitHTTPReadiness(ctx context.Context, exited func() bool, readiness *Readiness) error {
	deadline := time.Now().Add(8 * time.Second)
	address := Endpoint(readiness.Address, readiness.Port)
	var last []byte
	for time.Now().Before(deadline) {
		if exited() {
			return errorf("QTV encerrou antes de ficar pronto.")
		}
		if connection, err := net.DialTimeout("tcp", address, 400*time.Millisecond); err == nil {
			connection.SetDeadline(time.Now().Add(400 * time.Millisecond))
			if _, err := connection.Write([]byte("GET /nowplaying/ HTTP/1.0\r\nHost: x86qw.local\r\n\r\n")); err == nil {
				last, _ = io.ReadAll(io.LimitReader(connection, 1<<20))
				if qtvResponseReady(last, readiness.Upstream) {
					connection.Close()
					return nil
				}
			}
			connection.Close()
		}
		if err := sleepContext(ctx, 100*time.Millisecond); err != nil {
			return err
		}
	}
	if readiness.Upstream != "" && qtvResponseReady(last, "") {
		return errorf("QTV respondeu por HTTP, mas não registrou o upstream solicitado.")
	}
	return errorf("QTV não respondeu em http://%s/.", address)
}

// waitUDPReadiness mirrors wait_udp_readiness: alive for 1 s and holding the port.
func waitUDPReadiness(ctx context.Context, exited func() bool, readiness *Readiness) error {
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		if exited() {
			return errorf("QWFWD encerrou durante a inicialização.")
		}
		if err := sleepContext(ctx, 50*time.Millisecond); err != nil {
			return err
		}
	}
	probe, err := net.ListenPacket(network("udp", readiness.Address), Endpoint(readiness.Address, readiness.Port))
	if err != nil {
		return nil
	}
	probe.Close()
	return errorf("QWFWD permaneceu vivo, mas não ocupou a porta UDP solicitada.")
}

var oob = []byte{0xff, 0xff, 0xff, 0xff}

// applyStartupRcon mirrors apply_startup_rcon: status, serverinfo, then exec
// of the post-map cfg with the bootstrap password.
func applyStartupRcon(ctx context.Context, startup *StartupRcon) error {
	destination, err := net.ResolveUDPAddr(network("udp", startup.Address), Endpoint(startup.Address, startup.Port))
	if err != nil {
		return err
	}
	connection, err := net.ListenUDP(network("udp", startup.Address), nil)
	if err != nil {
		return err
	}
	defer connection.Close()
	buffer := make([]byte, 65535)
	receive := func(timeout time.Duration) ([]byte, error) {
		connection.SetReadDeadline(time.Now().Add(timeout))
		count, _, err := connection.ReadFromUDP(buffer)
		if err != nil {
			return nil, err
		}
		return append([]byte(nil), buffer[:count]...), nil
	}
	deadline := time.Now().Add(8 * time.Second)
	var response []byte
	for {
		if !time.Now().Before(deadline) {
			return errorf("MVDSV não respondeu em %s.", Endpoint(startup.Address, startup.Port))
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		connection.WriteToUDP(append(append([]byte{}, oob...), "status\n"...), destination)
		reply, err := receive(250 * time.Millisecond)
		if err != nil {
			var netErr net.Error
			if errors.As(err, &netErr) && netErr.Timeout() {
				continue
			}
			// Python only retries timeouts; ICMP refusals surface as errors.
			return errorf("Não foi possível iniciar um serviço: %v", err)
		}
		if bytes.HasPrefix(reply, oob) {
			response = reply
			break
		}
	}
	decoded := strings.ToLower(latin1(response))
	if !strings.Contains(decoded, strings.ToLower(startup.ExpectedMap)) {
		return errorf("MVDSV respondeu, mas não confirmou o mapa %s.", startup.ExpectedMap)
	}
	connection.WriteToUDP(append(append([]byte{}, oob...), fmt.Sprintf("rcon %s serverinfo\n", startup.Password)...), destination)
	serverinfo, err := receive(250 * time.Millisecond)
	if err != nil {
		return errorf("MVDSV não confirmou o gamecode carregado.")
	}
	if bytes.Contains(serverinfo, []byte("Bad rcon_password")) {
		return errorf("MVDSV rejeitou o preflight RCON local.")
	}
	combined := strings.ToLower(latin1(append(append(response, '\n'), serverinfo...)))
	if !strings.Contains(combined, strings.ToLower(startup.ExpectedGamedir)) {
		return errorf("MVDSV não confirmou o gamecode %s.", startup.ExpectedGamedir)
	}
	// KTX's sv_rconlim counts both password checks performed for each packet.
	if err := sleepContext(ctx, 1050*time.Millisecond); err != nil {
		return err
	}
	connection.WriteToUDP(append(append([]byte{}, oob...), fmt.Sprintf("rcon %s exec %s\n", startup.Password, startup.ConfigName)...), destination)
	reply, err := receive(2 * time.Second)
	if err != nil {
		return errorf("MVDSV não confirmou a configuração dedicada.")
	}
	if bytes.Contains(reply, []byte("Bad rcon_password")) {
		return errorf("MVDSV rejeitou a configuração dedicada por RCON local.")
	}
	return nil
}

func latin1(payload []byte) string {
	runes := make([]rune, len(payload))
	for index, value := range payload {
		runes[index] = rune(value)
	}
	return string(runes)
}
