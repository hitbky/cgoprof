package main

/*
static long long add_one_batch(int n) {
	long long total = 0;
	for (int i = 0; i < n; i++) {
		total += i + 1;
	}
	return total;
}
*/
import "C"

import "fmt"

func main() {
	total := int64(C.add_one_batch(1000000))
	fmt.Println(total)
}
