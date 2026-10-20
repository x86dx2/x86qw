//go:build windows

package services

import (
	"errors"
	"os"
	"os/exec"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

// Windows services follow supervisor_core.WindowsJobObject: every service
// starts suspended in its own process group, joins one kill-on-close Job
// Object owned by the controller and only then runs, so the whole tree ends
// with the controller even if it crashes.

// stillActive is STILL_ACTIVE, the exit code of a running process.
const stillActive = 259

// fileIdentity is the volume serial number and NTFS file index of handle.
func fileIdentity(handle windows.Handle) ([2]int64, error) {
	var info windows.ByHandleFileInformation
	if err := windows.GetFileInformationByHandle(handle, &info); err != nil {
		return [2]int64{}, err
	}
	return [2]int64{int64(info.VolumeSerialNumber), int64(uint64(info.FileIndexHigh)<<32 | uint64(info.FileIndexLow))}, nil
}

// identityAt is the identity of the entry at path itself (reparse points are
// not followed), matching what os.stat reports on Windows.
func identityAt(path string, info os.FileInfo) [2]int64 {
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return [2]int64{}
	}
	handle, err := windows.CreateFile(name, 0, windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE|windows.FILE_SHARE_DELETE,
		nil, windows.OPEN_EXISTING, windows.FILE_FLAG_OPEN_REPARSE_POINT|windows.FILE_FLAG_BACKUP_SEMANTICS, 0)
	if err != nil {
		return [2]int64{}
	}
	defer windows.CloseHandle(handle)
	identity, _ := fileIdentity(handle)
	return identity
}

func identityOfFile(file *os.File) ([2]int64, error) {
	return fileIdentity(windows.Handle(file.Fd()))
}

// processGroupStatus reports whether pid still runs; Windows has no process
// group probe, and services are contained by the Job Object instead.
func processGroupStatus(group int) string {
	handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(group))
	if err != nil {
		if errors.Is(err, windows.ERROR_INVALID_PARAMETER) {
			return "dead"
		}
		return "inconclusive"
	}
	defer windows.CloseHandle(handle)
	var code uint32
	if windows.GetExitCodeProcess(handle, &code) != nil {
		return "inconclusive"
	}
	if code == stillActive {
		return "alive"
	}
	return "dead"
}

// terminateProcess ends pid and waits up to timeout for it to exit.
func terminateProcess(pid int, timeout time.Duration) (bool, error) {
	handle, err := windows.OpenProcess(windows.PROCESS_TERMINATE|windows.SYNCHRONIZE, false, uint32(pid))
	if err != nil {
		if errors.Is(err, windows.ERROR_INVALID_PARAMETER) {
			return true, nil
		}
		return false, err
	}
	defer windows.CloseHandle(handle)
	if err := windows.TerminateProcess(handle, 1); err != nil && !errors.Is(err, windows.ERROR_ACCESS_DENIED) {
		return false, err
	}
	event, err := windows.WaitForSingleObject(handle, uint32(timeout/time.Millisecond))
	return err == nil && event == windows.WAIT_OBJECT_0, nil
}

// terminateRecordedProcess mirrors sessions.terminate_recorded_process.
func terminateRecordedProcess(entry map[string]any) (string, error) {
	pid, _ := intField(entry["pid"])
	switch processProbe(entry) {
	case "dead":
		return "already_dead", nil
	case "identity_mismatch":
		return "identity_mismatch", nil
	case "alive":
	default:
		return "", errorf("A identidade de um processo órfão ficou inconclusiva.")
	}
	exited, err := terminateProcess(pid, 4*time.Second)
	if err != nil {
		return "", errorf("Não foi possível encerrar o processo órfão PID %d.", pid)
	}
	if !exited {
		return "", errorf("O processo órfão PID %d não encerrou; estado preservado.", pid)
	}
	return "terminated", nil
}

// job is the controller's kill-on-close Job Object, created on first start.
var job struct {
	sync.Mutex
	handle windows.Handle
}

func serviceJob() (windows.Handle, error) {
	job.Lock()
	defer job.Unlock()
	if job.handle != 0 {
		return job.handle, nil
	}
	handle, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		return 0, errorf("Não foi possível criar o Job Object dos serviços (%v).", err)
	}
	var limits windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION
	limits.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	if _, err := windows.SetInformationJobObject(handle, windows.JobObjectExtendedLimitInformation,
		uintptr(unsafe.Pointer(&limits)), uint32(unsafe.Sizeof(limits))); err != nil {
		windows.CloseHandle(handle)
		return 0, errorf("Não foi possível configurar o Job Object (%v).", err)
	}
	job.handle = handle
	return handle, nil
}

// closeJob terminates whatever is left in the Job Object and closes it.
func closeJob() error {
	job.Lock()
	defer job.Unlock()
	if job.handle == 0 {
		return nil
	}
	terminateErr := windows.TerminateJobObject(job.handle, 1)
	closeErr := windows.CloseHandle(job.handle)
	job.handle = 0
	if closeErr != nil {
		return errorf("Não foi possível fechar o Job Object (%v).", closeErr)
	}
	if terminateErr != nil {
		return errorf("O Job Object foi fechado, mas o encerramento explícito da árvore falhou (%v).", terminateErr)
	}
	return nil
}

// resumeMainThread resumes the single thread of a process created suspended.
func resumeMainThread(pid int) error {
	snapshot, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPTHREAD, 0)
	if err != nil {
		return errorf("Não foi possível enumerar a thread inicial do PID %d (%v).", pid, err)
	}
	defer windows.CloseHandle(snapshot)
	var threads []uint32
	entry := windows.ThreadEntry32{Size: uint32(unsafe.Sizeof(windows.ThreadEntry32{}))}
	for err = windows.Thread32First(snapshot, &entry); err == nil; err = windows.Thread32Next(snapshot, &entry) {
		if entry.OwnerProcessID == uint32(pid) {
			threads = append(threads, entry.ThreadID)
		}
		entry.Size = uint32(unsafe.Sizeof(entry))
	}
	if !errors.Is(err, windows.ERROR_NO_MORE_FILES) {
		return errorf("Não foi possível enumerar a thread inicial do PID %d (%v).", pid, err)
	}
	if len(threads) != 1 {
		return errorf("A thread inicial suspensa do PID %d não pôde ser identificada de forma inequívoca.", pid)
	}
	thread, err := windows.OpenThread(windows.THREAD_SUSPEND_RESUME, false, threads[0])
	if err != nil {
		return errorf("Não foi possível abrir a thread inicial do PID %d (%v).", pid, err)
	}
	defer windows.CloseHandle(thread)
	previous, err := windows.ResumeThread(thread)
	if err != nil {
		return errorf("Não foi possível retomar o PID %d (%v).", pid, err)
	}
	if previous != 1 {
		return errorf("O PID %d apresentou contagem de suspensão inesperada (%d).", pid, previous)
	}
	return nil
}

// startDetached starts a service suspended in its own process group, adds
// it to the Job Object and resumes it (WindowsJobObject.start_process).
func startDetached(arguments []string, dir string, output *os.File) (*exec.Cmd, error) {
	jobHandle, err := serviceJob()
	if err != nil {
		return nil, err
	}
	command := exec.Command(arguments[0], arguments[1:]...)
	command.Dir = dir
	if output != nil {
		command.Stdout, command.Stderr = output, output
	}
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NEW_PROCESS_GROUP | windows.CREATE_SUSPENDED}
	if err := command.Start(); err != nil {
		return nil, err
	}
	pid := command.Process.Pid
	rollback := func(cause error, assigned bool) (*exec.Cmd, error) {
		if assigned {
			windows.TerminateJobObject(jobHandle, 1)
		} else {
			command.Process.Kill()
		}
		command.Wait()
		return nil, cause
	}
	process, err := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE, false, uint32(pid))
	if err != nil {
		return rollback(errorf("Não foi possível associar PID %d ao Job Object (%v).", pid, err), false)
	}
	assignErr := windows.AssignProcessToJobObject(jobHandle, process)
	windows.CloseHandle(process)
	if assignErr != nil {
		return rollback(errorf("Não foi possível associar PID %d ao Job Object (%v).", pid, assignErr), false)
	}
	if err := resumeMainThread(pid); err != nil {
		return rollback(err, true)
	}
	return command, nil
}

// stopGroups terminates the services in reverse order, waits up to four
// seconds, forces the stragglers and closes the Job Object (stop_processes
// followed by WindowsJobObject.close).
func stopGroups(commands []*exec.Cmd, exited map[int]bool) error {
	for index := len(commands) - 1; index >= 0; index-- {
		pid := commands[index].Process.Pid
		if !exited[pid] && processGroupStatus(pid) == "alive" {
			commands[index].Process.Kill()
		}
	}
	deadline := time.Now().Add(4 * time.Second)
	for _, command := range commands {
		pid := command.Process.Pid
		for time.Now().Before(deadline) && !exited[pid] && processGroupStatus(pid) == "alive" {
			time.Sleep(50 * time.Millisecond)
		}
		if processGroupStatus(pid) == "alive" {
			if _, err := terminateProcess(pid, time.Second); err != nil {
				return errorf("Não foi possível forçar o encerramento do PID %d.", pid)
			}
		}
	}
	return closeJob()
}

func exitCode(state *os.ProcessState) int { return state.ExitCode() }

var serviceSignals = []os.Signal{os.Interrupt}

func signalNumber(sig os.Signal) int { return 2 }

// spawnBackgroundController detaches one controller in a new process group
// and hands it the secrets exclusively through stdin.
func spawnBackgroundController(executable string, arguments []string, request []byte) (*exec.Cmd, error) {
	command := exec.Command(executable, arguments...)
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NEW_PROCESS_GROUP | windows.DETACHED_PROCESS}
	stdin, err := command.StdinPipe()
	if err != nil {
		return nil, errorf("Não foi possível criar o controlador em segundo plano: %v", err)
	}
	if err := command.Start(); err != nil {
		return nil, errorf("Não foi possível criar o controlador em segundo plano: %v", err)
	}
	_, writeErr := stdin.Write(request)
	if err := errors.Join(writeErr, stdin.Close()); err != nil {
		command.Process.Kill()
		command.Wait()
		return nil, errorf("O handoff privado do controlador falhou: %v", err)
	}
	return command, nil
}
