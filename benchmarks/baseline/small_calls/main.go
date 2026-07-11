package main

/*
static int add_one(int x) {
	return x + 1;
}
*/
import "C"

import "fmt"

func main() {
	total := 0
	for i := 0; i < 1000000; i++ {
		total += int(C.add_one(C.int(i)))
	}
	fmt.Println(total)
}
