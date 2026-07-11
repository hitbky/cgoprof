package main

/*
static int sum_ints(int *xs, int n) {
	int total = 0;
	for (int i = 0; i < n; i++) {
		total += xs[i];
	}
	return total;
}
*/
import "C"

import (
	"fmt"
	"time"
	"unsafe"

	prof "cgoprof/runtime_go/cgoprof"
)

func main() {
	defer prof.Close()
	data := []C.int{1, 2, 3, 4}
	total := 0
	for i := 0; i < 200000; i++ {
		checkStart := time.Now()
		ptr := unsafe.Pointer(&data[0])
		prof.PointerCheck("sum-ints-pointer", time.Since(checkStart)+50*time.Nanosecond)
		total += int(prof.Call("sum-ints-pointer", "sum_ints", func() C.int {
			return C.sum_ints((*C.int)(ptr), C.int(len(data)))
		}))
	}
	fmt.Println(total)
}
