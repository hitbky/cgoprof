module github.com/mattn/go-sqlite3

go 1.21

retract (
 [v2.0.0+incompatible, v2.0.7+incompatible] // Accidental; no major changes or features.
)

require cgoprof/runtime_go/cgoprof v0.0.0

replace cgoprof/runtime_go/cgoprof => /Users/ban/Documents/Projects/drpy/cgoprof/runtime_go/cgoprof
