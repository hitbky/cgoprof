//go:build !libsqlite3 || sqlite_serialize
// +build !libsqlite3 sqlite_serialize

package sqlite3

/*
#ifndef USE_LIBSQLITE3
#include "sqlite3-binding.h"
#else
#include <sqlite3.h>
#endif
#include <stdlib.h>
#include <stdint.h>
*/
import "C"

import (
	prof "cgoprof/runtime_go/cgoprof"
	"fmt"
	"math"
	"unsafe"
)

// Serialize returns a byte slice that is a serialization of the database.
//
// See https://www.sqlite.org/c3ref/serialize.html
func (c *SQLiteConn) Serialize(schema string) ([]byte, error) {
	if schema == "" {
		schema = "main"
	}
	var zSchema *C.char
	prof.Conversion("6af4cd2ade", "C.CString", int(len(schema)+1))
	prof.Memory("6af4cd2ade", "malloc", int(len(schema)+1))
	__cgoprof_end_1 := prof.BeginCall("6af4cd2ade", "CString")
	__cgoprof_ret_2 := C.CString(schema)
	__cgoprof_end_1()
	zSchema = __cgoprof_ret_2
	defer func() {
		prof.Memory("6af4cd2ade", "free", 0)
		__cgoprof_end_3 := prof.BeginCall("8b5f307e91", "free")
		C.free(unsafe.Pointer(zSchema))
		__cgoprof_end_3()
	}()

	var sz C.sqlite3_int64
	prof.PointerCheck("be97803eec", 50)
	__cgoprof_end_4 := prof.BeginCall("be97803eec", "sqlite3_serialize")
	__cgoprof_ret_5 := C.sqlite3_serialize(c.db, zSchema, &sz, 0)
	__cgoprof_end_4()
	ptr := __cgoprof_ret_5
	if ptr == nil {
		return nil, fmt.Errorf("serialize failed")
	}
	defer func() {
		__cgoprof_end_6 := prof.BeginCall("90ca1550e7", "sqlite3_free")
		C.sqlite3_free(unsafe.Pointer(ptr))
		__cgoprof_end_6()
	}()

	if sz > C.sqlite3_int64(math.MaxInt) {
		return nil, fmt.Errorf("serialized database is too large (%d bytes)", sz)
	}

	res := make([]byte, int(sz))
	copy(res, unsafe.Slice((*byte)(unsafe.Pointer(ptr)), int(sz)))
	return res, nil
}

// Deserialize causes the connection to disconnect from the current database and
// then re-open as an in-memory database based on the contents of the byte slice.
//
// See https://www.sqlite.org/c3ref/deserialize.html
func (c *SQLiteConn) Deserialize(b []byte, schema string) error {
	if schema == "" {
		schema = "main"
	}
	var zSchema *C.char
	prof.Conversion("922428fab5", "C.CString", int(len(schema)+1))
	prof.Memory("922428fab5", "malloc", int(len(schema)+1))
	__cgoprof_end_7 := prof.BeginCall("922428fab5", "CString")
	__cgoprof_ret_8 := C.CString(schema)
	__cgoprof_end_7()
	zSchema = __cgoprof_ret_8
	defer func() {
		prof.Memory("922428fab5", "free", 0)
		__cgoprof_end_9 := prof.BeginCall("15e1aa53f5", "free")
		C.free(unsafe.Pointer(zSchema))
		__cgoprof_end_9()
	}()
	__cgoprof_end_10 := prof.BeginCall("a94ed6cdc2", "sqlite3_malloc64")
	__cgoprof_ret_11 := C.sqlite3_malloc64(C.sqlite3_uint64(len(b)))
	__cgoprof_end_10()
	tmpBuf := (*C.uchar)(__cgoprof_ret_11)
	copy(unsafe.Slice((*byte)(unsafe.Pointer(tmpBuf)), len(b)), b)
	__cgoprof_end_12 := prof.BeginCall("6437d168ef", "sqlite3_deserialize")
	__cgoprof_ret_13 := C.sqlite3_deserialize(c.db, zSchema, tmpBuf, C.sqlite3_int64(len(b)),
		C.sqlite3_int64(len(b)), C.SQLITE_DESERIALIZE_FREEONCLOSE)
	__cgoprof_end_12()
	rc := __cgoprof_ret_13

	if rc != C.SQLITE_OK {
		return fmt.Errorf("deserialize failed with return %v", rc)
	}
	return nil
}
