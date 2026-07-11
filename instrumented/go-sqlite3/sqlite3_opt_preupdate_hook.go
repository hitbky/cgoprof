// Copyright (C) 2019 G.J.R. Timmer <gjr.timmer@gmail.com>.
// Copyright (C) 2018 segment.com <friends@segment.com>
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file.

//go:build sqlite_preupdate_hook
// +build sqlite_preupdate_hook

package sqlite3

/*
#cgo CFLAGS: -DSQLITE_ENABLE_PREUPDATE_HOOK
#cgo LDFLAGS: -lm

#ifndef USE_LIBSQLITE3
#include "sqlite3-binding.h"
#else
#include <sqlite3.h>
#endif
#include <stdlib.h>
#include <string.h>

void preUpdateHookTrampoline(void*, sqlite3 *, int, char *, char *, sqlite3_int64, sqlite3_int64);
*/
import "C"
import (
	prof "cgoprof/runtime_go/cgoprof"
	"errors"
	"unsafe"
)

// RegisterPreUpdateHook sets the pre-update hook for a connection.
//
// The callback is passed a SQLitePreUpdateData struct with the data for
// the update, as well as methods for fetching copies of impacted data.
//
// If there is an existing preupdate hook for this connection, it will be
// removed. If callback is nil the existing hook (if any) will be removed
// without creating a new one.
func (c *SQLiteConn) RegisterPreUpdateHook(callback func(SQLitePreUpdateData)) {
	if callback == nil {
		__cgoprof_end_1 := prof.BeginCall("df7ef5a27f", "sqlite3_preupdate_hook")
		C.sqlite3_preupdate_hook(c.db, nil, nil)
		__cgoprof_end_1()
	} else {
		__cgoprof_end_2 := prof.BeginCall("d1656d1ae0", "sqlite3_preupdate_hook")
		C.sqlite3_preupdate_hook(c.db, (*[0]byte)(unsafe.Pointer(C.preUpdateHookTrampoline)), unsafe.Pointer(newHandle(c, callback)))
		__cgoprof_end_2(

		// Depth returns the source path of the write, see sqlite3_preupdate_depth()
		)
	}
}

func (d *SQLitePreUpdateData) Depth() int {
	__cgoprof_end_3 := prof.BeginCall("4090bd71ad", "sqlite3_preupdate_depth")

	// Count returns the number of columns in the row
	__cgoprof_ret_4 := C.sqlite3_preupdate_depth(d.Conn.db)
	__cgoprof_end_3()
	return int(__cgoprof_ret_4)
}

func (d *SQLitePreUpdateData) Count() int {
	__cgoprof_end_5 := prof.BeginCall("c87395c66b", "sqlite3_preupdate_count")
	__cgoprof_ret_6 := C.sqlite3_preupdate_count(d.Conn.db)
	__cgoprof_end_5()
	return int(__cgoprof_ret_6)
}

func (d *SQLitePreUpdateData) row(dest []any, new bool) error {
	for i := 0; i < d.Count() && i < len(dest); i++ {
		var val *C.sqlite3_value
		var src any

		// Initially I tried making this just a function pointer argument, but
		// it's absurdly complicated to pass C function pointers.
		if new {
			prof.PointerCheck("f4a92d6e6e", 50)
			__cgoprof_end_7 := prof.BeginCall("f4a92d6e6e", "sqlite3_preupdate_new")
			C.sqlite3_preupdate_new(d.Conn.db, C.int(i), &val)
			__cgoprof_end_7()
		} else {
			prof.PointerCheck("ef89ed2ea2", 50)
			__cgoprof_end_8 := prof.BeginCall("ef89ed2ea2", "sqlite3_preupdate_old")
			C.sqlite3_preupdate_old(d.Conn.db, C.int(i), &val)
			__cgoprof_end_8()
		}
		__cgoprof_end_25 := prof.BeginCall("f74adf08d9", "sqlite3_value_type")
		__cgoprof_ret_26 := C.sqlite3_value_type(val)
		__cgoprof_end_25()
		switch __cgoprof_ret_26 {
		case C.SQLITE_INTEGER:
			__cgoprof_end_9 := prof.BeginCall("47a089267f", "sqlite3_value_int64")
			__cgoprof_ret_10 := C.sqlite3_value_int64(val)
			__cgoprof_end_9()
			src = int64(__cgoprof_ret_10)
		case C.SQLITE_FLOAT:
			__cgoprof_end_11 := prof.BeginCall("5ef6e2b842", "sqlite3_value_double")
			__cgoprof_ret_12 := C.sqlite3_value_double(val)
			__cgoprof_end_11()
			src = float64(__cgoprof_ret_12)
		case C.SQLITE_BLOB:
			__cgoprof_end_13 := prof.BeginCall("4015bc9457", "sqlite3_value_bytes")
			__cgoprof_ret_14 := C.sqlite3_value_bytes(val)
			__cgoprof_end_13()
			len := __cgoprof_ret_14
			__cgoprof_end_15 := prof.BeginCall("6c50f9ee83", "sqlite3_value_blob")
			__cgoprof_ret_16 := C.sqlite3_value_blob(val)
			__cgoprof_end_15()
			blobptr := __cgoprof_ret_16
			prof.Conversion("f33e9200f3", "C.GoBytes", int(len))
			__cgoprof_end_17 := prof.BeginCall("f33e9200f3", "GoBytes")
			__cgoprof_ret_18 := C.GoBytes(blobptr, len)
			__cgoprof_end_17()
			src = __cgoprof_ret_18
		case C.SQLITE_TEXT:
			__cgoprof_end_19 := prof.BeginCall("8ff4cb9d81", "sqlite3_value_bytes")
			__cgoprof_ret_20 := C.sqlite3_value_bytes(val)
			__cgoprof_end_19()
			len := __cgoprof_ret_20
			__cgoprof_end_21 := prof.BeginCall("8fe1dd1dc9", "sqlite3_value_text")
			__cgoprof_ret_22 := C.sqlite3_value_text(val)
			__cgoprof_end_21()
			cstrptr := unsafe.Pointer(__cgoprof_ret_22)
			prof.Conversion("06a24be397", "C.GoBytes", int(len))
			__cgoprof_end_23 := prof.BeginCall("06a24be397", "GoBytes")
			__cgoprof_ret_24 := C.GoBytes(cstrptr, len)
			__cgoprof_end_23()
			src = __cgoprof_ret_24
		case C.SQLITE_NULL:
			src = nil
		}

		err := convertAssign(&dest[i], src)
		if err != nil {
			return err
		}
	}

	return nil
}

// Old populates dest with the row data to be replaced. This works similar to
// database/sql's Rows.Scan()
func (d *SQLitePreUpdateData) Old(dest ...any) error {
	if d.Op == SQLITE_INSERT {
		return errors.New("There is no old row for INSERT operations")
	}
	return d.row(dest, false)
}

// New populates dest with the replacement row data. This works similar to
// database/sql's Rows.Scan()
func (d *SQLitePreUpdateData) New(dest ...any) error {
	if d.Op == SQLITE_DELETE {
		return errors.New("There is no new row for DELETE operations")
	}
	return d.row(dest, true)
}
