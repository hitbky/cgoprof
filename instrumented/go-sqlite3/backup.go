// Copyright (C) 2019 Yasuhiro Matsumoto <mattn.jp@gmail.com>.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file.

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
	"runtime"
	"unsafe"
)

// SQLiteBackup implement interface of Backup.
type SQLiteBackup struct {
	b *C.sqlite3_backup
}

// Backup make backup from src to dest.
func (destConn *SQLiteConn) Backup(dest string, srcConn *SQLiteConn, src string) (*SQLiteBackup, error) {
	prof.Conversion("d0ebf15a5a", "C.CString", int(len(dest)+1))
	prof.Memory("d0ebf15a5a", "malloc", int(len(dest)+1))
	__cgoprof_end_1 := prof.BeginCall("d0ebf15a5a", "CString")
	__cgoprof_ret_2 := C.CString(dest)
	__cgoprof_end_1()
	destptr := __cgoprof_ret_2
	defer func() {
		prof.Memory("d0ebf15a5a", "free", 0)
		__cgoprof_end_3 := prof.BeginCall("8be59766bd", "free")
		C.free(unsafe.Pointer(destptr))
		__cgoprof_end_3()
	}()
	prof.Conversion("d750070a4c", "C.CString", int(len(src)+1))
	prof.Memory("d750070a4c", "malloc", int(len(src)+1))
	__cgoprof_end_4 := prof.BeginCall("d750070a4c", "CString")
	__cgoprof_ret_5 := C.CString(src)
	__cgoprof_end_4()
	srcptr := __cgoprof_ret_5
	defer func() {
		prof.Memory("d750070a4c", "free", 0)
		__cgoprof_end_6 := prof.BeginCall("50e16a72b7", "free")
		C.free(unsafe.Pointer(srcptr))
		__cgoprof_end_6()
	}()

	if b := C.sqlite3_backup_init(destConn.db, destptr, srcConn.db, srcptr); b != nil {
		bb := &SQLiteBackup{b: b}
		runtime.SetFinalizer(bb, (*SQLiteBackup).Finish)
		return bb, nil
	}
	return nil, destConn.lastError()
}

// Step to backs up for one step. Calls the underlying `sqlite3_backup_step`
// function.  This function returns a boolean indicating if the backup is done
// and an error signalling any other error. Done is returned if the underlying
// C function returns SQLITE_DONE (Code 101)
func (b *SQLiteBackup) Step(p int) (bool, error) {
	__cgoprof_end_7 := prof.BeginCall("3f3d25bbbd", "sqlite3_backup_step")
	__cgoprof_ret_8 := C.sqlite3_backup_step(b.b, C.int(p))
	__cgoprof_end_7()
	ret := __cgoprof_ret_8
	if ret == C.SQLITE_DONE {
		return true, nil
	} else if ret != 0 && ret != C.SQLITE_LOCKED && ret != C.SQLITE_BUSY {
		return false, Error{Code: ErrNo(ret)}
	}
	return false, nil
}

// Remaining return whether have the rest for backup.
func (b *SQLiteBackup) Remaining() int {
	__cgoprof_end_9 := prof.BeginCall("883353b5f5", "sqlite3_backup_remaining")

	// PageCount return count of pages.
	__cgoprof_ret_10 := C.sqlite3_backup_remaining(b.b)
	__cgoprof_end_9()
	return int(__cgoprof_ret_10)
}

func (b *SQLiteBackup) PageCount() int {
	__cgoprof_end_11 := prof.BeginCall("bf7b3454e9", "sqlite3_backup_pagecount")

	// Finish close backup.
	__cgoprof_ret_12 := C.sqlite3_backup_pagecount(b.b)
	__cgoprof_end_11()
	return int(__cgoprof_ret_12)
}

func (b *SQLiteBackup) Finish() error {
	return b.Close()
}

// Close close backup.
func (b *SQLiteBackup) Close() error {
	__cgoprof_end_13 := prof.BeginCall("43ab48510c",

		// sqlite3_backup_finish() never fails, it just returns the
		// error code from previous operations, so clean up before
		// checking and returning an error
		"sqlite3_backup_finish")
	__cgoprof_ret_14 := C.sqlite3_backup_finish(b.b)
	__cgoprof_end_13()
	ret := __cgoprof_ret_14

	b.b = nil
	runtime.SetFinalizer(b, nil)

	if ret != 0 {
		return Error{Code: ErrNo(ret)}
	}
	return nil
}
