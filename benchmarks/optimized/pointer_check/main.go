package main

/*
static int sum_ints_repeated(int *xs, int n, int repeat) {
	int total = 0;
	for (int r = 0; r < repeat; r++) {
		for (int i = 0; i < n; i++) {
			total += xs[i];
		}
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
	total := int(C.sum_ints_repeated((*C.int)(unsafe.Pointer(&data[0])), C.int(len(data)), 200000))
	fmt.Println(total)
}
