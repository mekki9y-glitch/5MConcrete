[app]
title = 5M Concrete
package.name = concreteapp
package.domain = org.test

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf

version = 1.0

requirements = python3,kivy==2.2.1,reportlab,sqlite3

orientation = portrait
fullscreen = 0

android.permissions = WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE

android.api = 33
android.minapi = 21
android.ndk = 25b
android.archs = arm64-v8a,armeabi-v7a

[buildozer]
log_level = 2
warn_on_root = 1