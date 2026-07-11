package main

/*
#include <stdlib.h>
#include <string.h>

static int consume_string(const char *s) {
	return (int)strlen(s);
}
*/
import "C"

import (
	"fmt"
	"unsafe"

	prof "cgoprof/runtime_go/cgoprof"
)

func main() {
	defer prof.Close()
	total := 0
	for i := 0; i < 200000; i++ {
		s := "stable-key-used-in-a-hot-loop"
		prof.Conversion("cstring-hot-loop", "C.CString", len(s)+1)
		cs := C.CString(s)
		prof.Memory("cstring-hot-loop", "malloc", len(s)+1)
		total += int(prof.Call("cstring-hot-loop", "consume_string", func() C.int {
			return C.consume_string(cs)
		}))
		C.free(unsafe.Pointer(cs))
		prof.Memory("cstring-hot-loop", "free", len(s)+1)
	}
	fmt.Println(total)
}
