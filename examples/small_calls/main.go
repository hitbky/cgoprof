package main

/*
static int add_one(int x) {
	return x + 1;
}
*/
import "C"

import (
	"fmt"
	"time"

	prof "cgoprof/runtime_go/cgoprof"
)

func main() {
	defer prof.Close()
	total := 0
	for i := 0; i < 1000000; i++ {
		total += int(prof.CallWithCost("small-add-one", "add_one", 0, 10*time.Nanosecond, func() C.int {
			return C.add_one(C.int(i))
		}))
	}
	fmt.Println(total)
}
