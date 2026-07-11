package cgoprof

import (
	"encoding/json"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"
)

type Event struct {
	Kind        string            `json:"kind"`
	SiteID      string            `json:"site_id"`
	TimestampNS int64             `json:"timestamp_ns"`
	DurationNS  int64             `json:"duration_ns,omitempty"`
	Goroutine   int64             `json:"goroutine,omitempty"`
	Function    string            `json:"function,omitempty"`
	Source      string            `json:"source,omitempty"`
	Bytes       int               `json:"bytes,omitempty"`
	Detail      map[string]string `json:"detail,omitempty"`
}

var recorder = newRecorder()

type eventRecorder struct {
	mu  sync.Mutex
	out *os.File
	enc *json.Encoder
}

func newRecorder() *eventRecorder {
	path := os.Getenv("CGOPROF_OUT")
	if path == "" {
		path = "cgoprof.jsonl"
	}
	out, err := os.Create(path)
	if err != nil {
		panic(err)
	}
	return &eventRecorder{out: out, enc: json.NewEncoder(out)}
}

func Close() {
	recorder.mu.Lock()
	defer recorder.mu.Unlock()
	_ = recorder.out.Close()
}

func Call[T any](siteID string, cSymbol string, op func() T) T {
	start := time.Now()
	result := op()
	elapsed := time.Since(start)
	recordCall(siteID, cSymbol, start, elapsed, 0, 0)
	return result
}

func CallWithCost[T any](siteID string, cSymbol string, boundary time.Duration, cWork time.Duration, op func() T) T {
	start := time.Now()
	result := op()
	elapsed := time.Since(start)
	recordCall(siteID, cSymbol, start, elapsed, boundary.Nanoseconds(), cWork.Nanoseconds())
	return result
}

func recordCall(siteID string, cSymbol string, start time.Time, elapsed time.Duration, boundaryNS int64, cWorkNS int64) {
	detail := map[string]string{
		"c_symbol": cSymbol,
	}
	if boundaryNS > 0 {
		detail["boundary_ns"] = strconv.FormatInt(boundaryNS, 10)
	}
	if cWorkNS > 0 {
		detail["c_work_ns"] = strconv.FormatInt(cWorkNS, 10)
	}
	if elapsed >= time.Millisecond {
		detail["blocking_candidate"] = "true"
	}
	Record(Event{
		Kind:        "cgo_call",
		SiteID:      siteID,
		TimestampNS: start.UnixNano(),
		DurationNS:  elapsed.Nanoseconds(),
		Goroutine:   goid(),
		Function:    cSymbol,
		Detail:      detail,
	})
	if elapsed >= time.Millisecond {
		SchedulerBlock(siteID, elapsed)
	}
}

func CallVoid(siteID string, cSymbol string, op func()) {
	start := time.Now()
	op()
	elapsed := time.Since(start)
	recordCall(siteID, cSymbol, start, elapsed, 0, 0)
}

func BeginCall(siteID string, cSymbol string) func() {
	start := time.Now()
	return func() {
		recordCall(siteID, cSymbol, start, time.Since(start), 0, 0)
	}
}

func Conversion(siteID string, op string, bytes int) {
	direction := "unknown"
	switch op {
	case "C.CString", "C.CBytes":
		direction = "go_to_c"
	case "C.GoString", "C.GoStringN", "C.GoBytes":
		direction = "c_to_go"
	}
	Record(Event{
		Kind:        "conversion",
		SiteID:      siteID,
		TimestampNS: time.Now().UnixNano(),
		Goroutine:   goid(),
		Bytes:       bytes,
		Detail: map[string]string{
			"op":        op,
			"direction": direction,
		},
	})
}

func Memory(siteID string, op string, bytes int) {
	Record(Event{
		Kind:        "memory",
		SiteID:      siteID,
		TimestampNS: time.Now().UnixNano(),
		Goroutine:   goid(),
		Bytes:       bytes,
		Detail: map[string]string{
			"op": op,
		},
	})
}

func PointerCheck(siteID string, duration time.Duration) {
	Record(Event{
		Kind:        "pointer_check",
		SiteID:      siteID,
		TimestampNS: time.Now().UnixNano(),
		DurationNS:  duration.Nanoseconds(),
		Goroutine:   goid(),
	})
}

func Callback(siteID string, source string, duration time.Duration) {
	Record(Event{
		Kind:        "callback",
		SiteID:      siteID,
		TimestampNS: time.Now().UnixNano(),
		DurationNS:  duration.Nanoseconds(),
		Goroutine:   goid(),
		Source:      source,
	})
}

func SchedulerBlock(siteID string, duration time.Duration) {
	Record(Event{
		Kind:        "scheduler",
		SiteID:      siteID,
		TimestampNS: time.Now().UnixNano(),
		DurationNS:  duration.Nanoseconds(),
		Goroutine:   goid(),
		Detail: map[string]string{
			"op": "block",
		},
	})
}

func Record(event Event) {
	recorder.mu.Lock()
	defer recorder.mu.Unlock()
	if err := recorder.enc.Encode(event); err != nil {
		panic(err)
	}
}

func goid() int64 {
	var buf [64]byte
	n := runtime.Stack(buf[:], false)
	fields := strings.Fields(string(buf[:n]))
	if len(fields) < 2 {
		return 0
	}
	id, err := strconv.ParseInt(fields[1], 10, 64)
	if err != nil {
		return 0
	}
	return id
}
