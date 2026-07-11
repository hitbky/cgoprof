package main

/*
extern void goCallback(int);
void call_back_many(int n);
*/
import "C"

import (
	"time"

	prof "cgoprof/runtime_go/cgoprof"
)

//export goCallback
func goCallback(v C.int) {
	start := time.Now()
	_ = int(v) + 1
	prof.Callback("callback-many", "call_back_many", time.Since(start)+50*time.Nanosecond)
}

func main() {
	defer prof.Close()
	prof.CallVoid("callback-many", "call_back_many", func() {
		C.call_back_many(100000)
	})
}
