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
	"unsafe"
)

func main() {
	data := []C.int{1, 2, 3, 4}
	total := 0
	for i := 0; i < 200000; i++ {
		total += int(C.sum_ints((*C.int)(unsafe.Pointer(&data[0])), C.int(len(data))))
	}
	fmt.Println(total)
}
