// Copyright (C) 2019 Yasuhiro Matsumoto <mattn.jp@gmail.com>.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file.

//go:build cgo && sqlite_unlock_notify
// +build cgo,sqlite_unlock_notify

package sqlite3

/*
#cgo CFLAGS: -DSQLITE_ENABLE_UNLOCK_NOTIFY

#include <stdlib.h>
#ifndef USE_LIBSQLITE3
#include "sqlite3-binding.h"
#else
#include <sqlite3.h>
#endif

extern void unlock_notify_callback(void *arg, int argc);
*/
import "C"

import (
	prof "cgoprof/runtime_go/cgoprof"
	"fmt"
	"math"
	"sync"
	"time"
	"unsafe"
)

type unlock_notify_table struct {
	sync.Mutex
	seqnum uint
	table  map[uint]chan struct{}
}

var unt unlock_notify_table = unlock_notify_table{table: make(map[uint]chan struct{})}

func (t *unlock_notify_table) add(c chan struct{}) uint {
	t.Lock()
	defer t.Unlock()
	h := t.seqnum
	t.table[h] = c
	t.seqnum++
	return h
}

func (t *unlock_notify_table) remove(h uint) {
	t.Lock()
	defer t.Unlock()
	delete(t.table, h)
}

func (t *unlock_notify_table) get(h uint) chan struct{} {
	t.Lock()
	defer t.Unlock()
	c, ok := t.table[h]
	if !ok {
		panic(fmt.Sprintf("Non-existent key for unlcok-notify channel: %d", h))
	}
	return c
}

//export unlock_notify_callback
func unlock_notify_callback(argv unsafe.Pointer, argc C.int) {
	__cgoprof_callbackStart_1 := time.Now()
	defer func() {
		prof.Callback("499faf621d", "C", time.Since(__cgoprof_callbackStart_1))
	}()
	for i := 0; i < int(argc); i++ {
		parg := ((*(*[(math.MaxInt32 - 1) / unsafe.Sizeof((*C.uint)(nil))]*[1]uint)(argv))[i])
		arg := *parg
		h := arg[0]
		c := unt.get(h)
		c <- struct{}{}
	}
}

//export unlock_notify_wait
func unlock_notify_wait(db *C.sqlite3) C.int {
	__cgoprof_callbackStart_2 :=
		// It has to be a bufferred channel to not block in sqlite_unlock_notify
		// as sqlite_unlock_notify could invoke the callback before it returns.
		time.Now()
	defer func() {
		prof.Callback("e9f466508c", "C", time.Since(__cgoprof_callbackStart_2))
	}()

	c := make(chan struct{}, 1)
	defer close(c)

	h := unt.add(c)
	defer unt.remove(h)
	prof.Memory("f9c198aa22", "malloc", int(C.size_t(unsafe.Sizeof(uint(0)))))
	__cgoprof_end_3 := prof.BeginCall("f9c198aa22", "malloc")
	__cgoprof_ret_4 := C.malloc(C.size_t(unsafe.Sizeof(uint(0))))
	__cgoprof_end_3()
	pargv := __cgoprof_ret_4
	defer func() {
		prof.Memory("f9c198aa22", "free", 0)
		__cgoprof_end_5 := prof.BeginCall("d94420c47e", "free")
		C.free(pargv)
		__cgoprof_end_5()
	}()

	argv := (*[1]uint)(pargv)
	argv[0] = h
	if rv := C.sqlite3_unlock_notify(db, (*[0]byte)(C.unlock_notify_callback), unsafe.Pointer(pargv)); rv != C.SQLITE_OK {
		return rv
	}

	<-c

	return C.SQLITE_OK
}
