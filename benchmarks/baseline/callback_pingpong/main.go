package main

/*
extern void goCallback(int);
void call_back_many(int n);
*/
import "C"

import "fmt"

var total int

//export goCallback
func goCallback(v C.int) {
	total += int(v) + 1
}

func main() {
	C.call_back_many(100000)
	fmt.Println(total)
}
