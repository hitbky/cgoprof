package main

/*
static long long sum_many(int n) {
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
	total := int64(C.sum_many(100000))
	fmt.Println(total)
}
