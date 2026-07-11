// Copyright (C) 2019 Yasuhiro Matsumoto <mattn.jp@gmail.com>.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file.

//go:build !sqlite_omit_load_extension
// +build !sqlite_omit_load_extension

package sqlite3

/*
#ifndef USE_LIBSQLITE3
#include "sqlite3-binding.h"
#else
#include <sqlite3.h>
#endif
#include <stdlib.h>
*/
import "C"
import (
	prof "cgoprof/runtime_go/cgoprof"
	"errors"
	"unsafe"
)

func (c *SQLiteConn) loadExtensions(extensions []string) error {
	__cgoprof_end_1 := prof.BeginCall("1bfacbce85", "sqlite3_enable_load_extension")
	__cgoprof_ret_2 := C.sqlite3_enable_load_extension(c.db, 1)
	__cgoprof_end_1()
	rv := __cgoprof_ret_2
	if rv != C.SQLITE_OK {
		__cgoprof_end_3 := prof.BeginCall("24fdcdbeb9", "sqlite3_errmsg")
		__cgoprof_ret_4 := C.sqlite3_errmsg(c.db)
		__cgoprof_end_3()
		prof.Conversion("cb72198fe2", "C.GoString", int(0))
		__cgoprof_end_5 := prof.BeginCall("cb72198fe2", "GoString")
		__cgoprof_ret_6 := C.GoString(__cgoprof_ret_4)
		__cgoprof_end_5()
		return errors.New(__cgoprof_ret_6)
	}

	for _, extension := range extensions {
		if err := c.loadExtension(extension, nil); err != nil {
			__cgoprof_end_7 := prof.BeginCall("d8fdaed3bd", "sqlite3_enable_load_extension")
			C.sqlite3_enable_load_extension(c.db, 0)
			__cgoprof_end_7()
			return err
		}
	}
	__cgoprof_end_8 := prof.BeginCall("e12e5a8a91", "sqlite3_enable_load_extension")
	__cgoprof_ret_9 := C.sqlite3_enable_load_extension(c.db, 0)
	__cgoprof_end_8()
	rv = __cgoprof_ret_9
	if rv != C.SQLITE_OK {
		__cgoprof_end_10 := prof.BeginCall("c167e2281d", "sqlite3_errmsg")
		__cgoprof_ret_11 := C.sqlite3_errmsg(c.db)
		__cgoprof_end_10()
		prof.

			// LoadExtension load the sqlite3 extension.
			Conversion("142de87e1e", "C.GoString", int(0))
		__cgoprof_end_12 := prof.BeginCall("142de87e1e", "GoString")
		__cgoprof_ret_13 := C.GoString(__cgoprof_ret_11)
		__cgoprof_end_12()
		return errors.New(__cgoprof_ret_13)
	}

	return nil
}

func (c *SQLiteConn) LoadExtension(lib string, entry string) error {
	__cgoprof_end_14 := prof.BeginCall("435a1384bb", "sqlite3_enable_load_extension")
	__cgoprof_ret_15 := C.sqlite3_enable_load_extension(c.db, 1)
	__cgoprof_end_14()
	rv := __cgoprof_ret_15
	if rv != C.SQLITE_OK {
		__cgoprof_end_16 := prof.BeginCall("bdfdb16ef7", "sqlite3_errmsg")
		__cgoprof_ret_17 := C.sqlite3_errmsg(c.db)
		__cgoprof_end_16()
		prof.Conversion("480b5dbefe", "C.GoString", int(0))
		__cgoprof_end_18 := prof.BeginCall("480b5dbefe", "GoString")
		__cgoprof_ret_19 := C.GoString(__cgoprof_ret_17)
		__cgoprof_end_18()
		return errors.New(__cgoprof_ret_19)
	}

	if err := c.loadExtension(lib, &entry); err != nil {
		__cgoprof_end_20 := prof.BeginCall("61792d2e8a", "sqlite3_enable_load_extension")
		C.sqlite3_enable_load_extension(c.db, 0)
		__cgoprof_end_20()
		return err
	}
	__cgoprof_end_21 := prof.BeginCall("40f24b1588", "sqlite3_enable_load_extension")
	__cgoprof_ret_22 := C.sqlite3_enable_load_extension(c.db, 0)
	__cgoprof_end_21()
	rv = __cgoprof_ret_22
	if rv != C.SQLITE_OK {
		__cgoprof_end_23 := prof.BeginCall("5e7e5d09e0", "sqlite3_errmsg")
		__cgoprof_ret_24 := C.sqlite3_errmsg(c.db)
		__cgoprof_end_23()
		prof.Conversion("f18c922ae2", "C.GoString", int(0))
		__cgoprof_end_25 := prof.BeginCall("f18c922ae2", "GoString")
		__cgoprof_ret_26 := C.GoString(__cgoprof_ret_24)
		__cgoprof_end_25()
		return errors.New(__cgoprof_ret_26)
	}

	return nil
}

func (c *SQLiteConn) loadExtension(lib string, entry *string) error {
	prof.Conversion("fcd50a0269", "C.CString", int(len(lib)+1))
	prof.Memory("fcd50a0269", "malloc", int(len(lib)+1))
	__cgoprof_end_27 := prof.BeginCall("fcd50a0269", "CString")
	__cgoprof_ret_28 := C.CString(lib)
	__cgoprof_end_27()
	clib := __cgoprof_ret_28
	defer func() {
		prof.Memory("fcd50a0269", "free", 0)
		__cgoprof_end_29 := prof.BeginCall("daa1b8be76", "free")
		C.free(unsafe.Pointer(clib))
		__cgoprof_end_29()
	}()

	var centry *C.char
	if entry != nil {
		prof.Conversion("43e9bb6ebc", "C.CString", int(len(*entry)+1))
		prof.Memory("43e9bb6ebc", "malloc", int(len(*entry)+1))
		__cgoprof_end_30 := prof.BeginCall("43e9bb6ebc", "CString")
		__cgoprof_ret_31 := C.CString(*entry)
		__cgoprof_end_30()
		centry = __cgoprof_ret_31
		defer func() {
			prof.Memory("43e9bb6ebc", "free", 0)
			__cgoprof_end_32 := prof.BeginCall("9df98a8939", "free")
			C.free(unsafe.Pointer(centry))
			__cgoprof_end_32()
		}()
	}

	var errMsg *C.char
	defer func() {
		__cgoprof_end_33 := prof.BeginCall("dfc28bb4fb", "sqlite3_free")
		C.sqlite3_free(unsafe.Pointer(errMsg))
		__cgoprof_end_33()
	}()
	prof.PointerCheck("420020a4a1", 50)
	__cgoprof_end_34 := prof.BeginCall("420020a4a1", "sqlite3_load_extension")
	__cgoprof_ret_35 := C.sqlite3_load_extension(c.db, clib, centry, &errMsg)
	__cgoprof_end_34()
	rv := __cgoprof_ret_35
	if rv != C.SQLITE_OK {
		prof.Conversion("8ba0674b69", "C.GoString", int(0))
		__cgoprof_end_36 := prof.BeginCall("8ba0674b69", "GoString")
		__cgoprof_ret_37 := C.GoString(errMsg)
		__cgoprof_end_36()
		return errors.New(__cgoprof_ret_37)
	}

	return nil
}
