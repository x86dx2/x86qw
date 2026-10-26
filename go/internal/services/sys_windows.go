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

// terminateRecordedProcess ends a journaled process once it is confirmed to
// be the one recorded.
func terminateRecordedProcess(entry map[string]any) (string, error) {
	pid, _ := intField(entry["pid"])
	switch processProbe(entry) {
	case "dead":
		return "already_dead", nil
	case "identity_mismatch":
		return "identity_mismatch", nil
	case "alive":
	default:
		return "", errorf("The identity of an orphaned process is inconclusive.")
	}
	exited, err := terminateProcess(pid, 4*time.Second)
	if err != nil {
		return "", errorf("Could not stop orphaned process PID %d.", pid)
	}
	if !exited {
		return "", errorf("Orphaned process PID %d did not stop; its state was kept.", pid)
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
		return 0, errorf("Could not create the services' Job Object (%v).", err)
	}
	var limits windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION
	limits.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	if _, err := windows.SetInformationJobObject(handle, windows.JobObjectExtendedLimitInformation,
		uintptr(unsafe.Pointer(&limits)), uint32(unsafe.Sizeof(limits))); err != nil {
		windows.CloseHandle(handle)
		return 0, errorf("Could not configure the Job Object (%v).", err)
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
		return errorf("Could not close the Job Object (%v).", closeErr)
	}
	if terminateErr != nil {
		return errorf("The Job Object was closed, but stopping the process tree explicitly failed (%v).", terminateErr)
	}
	return nil
}

// resumeMainThread resumes the single thread of a process created suspended.
func resumeMainThread(pid int) error {
	snapshot, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPTHREAD, 0)
	if err != nil {
		return errorf("Could not enumerate the initial thread of PID %d (%v).", pid, err)
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
		return errorf("Could not enumerate the initial thread of PID %d (%v).", pid, err)
	}
	if len(threads) != 1 {
		return errorf("The suspended initial thread of PID %d could not be identified unambiguously.", pid)
	}
	thread, err := windows.OpenThread(windows.THREAD_SUSPEND_RESUME, false, threads[0])
	if err != nil {
		return errorf("Could not open the initial thread of PID %d (%v).", pid, err)
	}
	defer windows.CloseHandle(thread)
	previous, err := windows.ResumeThread(thread)
	if err != nil {
		return errorf("Could not resume PID %d (%v).", pid, err)
	}
	if previous != 1 {
		return errorf("PID %d had an unexpected suspend count (%d).", pid, previous)
	}
	return nil
}

// startDetached starts a service suspended in its own process group, adds
// it to the Job Object and resumes it (WindowsJobObject.start_process).
func startDetached(arguments []string, dir string, output *os.File, hidden bool) (*exec.Cmd, error) {
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
		return rollback(errorf("Could not assign PID %d to the Job Object (%v).", pid, err), false)
	}
	assignErr := windows.AssignProcessToJobObject(jobHandle, process)
	windows.CloseHandle(process)
	if assignErr != nil {
		return rollback(errorf("Could not assign PID %d to the Job Object (%v).", pid, assignErr), false)
	}
	if err := resumeMainThread(pid); err != nil {
		return rollback(err, true)
	}
	if hidden {
		go hideWindows(uint32(pid))
	}
	return command, nil
}

var (
	enumWindows     = windows.NewLazySystemDLL("user32.dll").NewProc("EnumWindows")
	showWindowAsync = windows.NewLazySystemDLL("user32.dll").NewProc("ShowWindowAsync")
	// hideWindow hides the visible windows of the process its parameter
	// names; one callback serves every server, as callbacks are never freed.
	hideWindow = sync.OnceValue(func() uintptr {
		return windows.NewCallback(func(window windows.HWND, pid uintptr) uintptr {
			var owner uint32
			windows.GetWindowThreadProcessId(window, &owner)
			if uintptr(owner) == pid && windows.IsWindowVisible(window) {
				showWindowAsync.Call(uintptr(window), windows.SW_HIDE)
			}
			return 1
		})
	})
)

// hideWindows hides what pid shows during its first seconds. MVDSV opens a
// window of its own, visible from creation whatever the STARTUPINFO says,
// and closing it ends the whole stack; MVDSV itself hides it the same way
// when it starts minimized.
func hideWindows(pid uint32) {
	// The open handle keeps pid from naming another process meanwhile.
	process, err := windows.OpenProcess(windows.SYNCHRONIZE, false, pid)
	if err != nil {
		return
	}
	defer windows.CloseHandle(process)
	for deadline := time.Now().Add(10 * time.Second); time.Now().Before(deadline); {
		enumWindows.Call(hideWindow(), uintptr(pid))
		if event, _ := windows.WaitForSingleObject(process, 50); event != uint32(windows.WAIT_TIMEOUT) {
			return
		}
	}
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
				return errorf("Could not force-stop PID %d.", pid)
			}
		}
	}
	return closeJob()
}

func exitCode(state *os.ProcessState) int { return state.ExitCode() }

// serviceSignals end a stack: Ctrl+C, and the console closing, a logoff or
// a shutdown, which Go delivers as SIGTERM.
var serviceSignals = []os.Signal{os.Interrupt, syscall.SIGTERM}

func signalNumber(sig os.Signal) int {
	if sig == syscall.SIGTERM {
		return 15
	}
	return 2
}

// spawnBackgroundController detaches one controller in a new process group
// and hands it the secrets exclusively through stdin.
func spawnBackgroundController(executable string, arguments []string, request []byte) (*exec.Cmd, error) {
	command := exec.Command(executable, arguments...)
	// A hidden console rather than none: the servers it starts inherit it
	// instead of each opening a window that ends the session when closed.
	command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NEW_PROCESS_GROUP | windows.CREATE_NO_WINDOW}
	stdin, err := command.StdinPipe()
	if err != nil {
		return nil, errorf("Could not create the background controller: %v", err)
	}
	if err := command.Start(); err != nil {
		return nil, errorf("Could not create the background controller: %v", err)
	}
	_, writeErr := stdin.Write(request)
	if err := errors.Join(writeErr, stdin.Close()); err != nil {
		command.Process.Kill()
		command.Wait()
		return nil, errorf("The controller's private handoff failed: %v", err)
	}
	return command, nil
}
