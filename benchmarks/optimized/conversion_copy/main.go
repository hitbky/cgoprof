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
)

func main() {
	s := "stable-key-used-in-a-hot-loop"
	cs := C.CString(s)
	defer C.free(unsafe.Pointer(cs))

	total := 0
	for i := 0; i < 200000; i++ {
		total += int(C.consume_string(cs))
	}
	fmt.Println(total)
}
