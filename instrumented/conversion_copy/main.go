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
		prof.Conversion("c747adc126", "C.CString", len(s)+1)
		prof.Memory("c747adc126", "malloc", len(s)+1)
		__cgoprof_end_1 := prof.BeginCall("c747adc126", "CString")
		__cgoprof_ret_2 := C.CString(s)
		__cgoprof_end_1()
		cs := __cgoprof_ret_2
		prof.Memory("cstring-hot-loop", "malloc", len(s)+1)
		total += int(prof.Call("cstring-hot-loop", "consume_string", func() C.int {
			return C.consume_string(cs)
		}))
		prof.Memory("49802488f4", "free", 0)
		prof.PointerCheck("49802488f4", 50)
		__cgoprof_end_3 := prof.BeginCall("49802488f4", "free")
		C.free(unsafe.Pointer(cs))
		__cgoprof_end_3()
		prof.Memory("cstring-hot-loop", "free", len(s)+1)
	}
	fmt.Println(total)
}
