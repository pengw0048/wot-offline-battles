# Included from the pinned BigWorld client/CMakeLists.txt after bwclient exists.
if(NOT TARGET bwclient)
    message(FATAL_ERROR "The standalone application must be linked into bwclient")
endif()

set(WOT_PROJECTILE_SOURCES
    "${CMAKE_CURRENT_LIST_DIR}/projectile_scene.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/projectile_motion.cpp")
target_sources(bwclient PRIVATE "${CMAKE_CURRENT_LIST_DIR}/standalone_app.cpp"
    ${WOT_PROJECTILE_SOURCES})
target_include_directories(bwclient PRIVATE "${BW_SOURCE_DIR}/client"
    "${CMAKE_CURRENT_LIST_DIR}/../include")
# These sources are appended after the legacy client's PCH source enumeration.
# Keep its C++14/PCH contract intact; the recovered numerical core uses C++17.
if(MSVC)
    set_source_files_properties(${WOT_PROJECTILE_SOURCES}
        PROPERTIES COMPILE_OPTIONS "/std:c++17;/fp:strict")
else()
    set_source_files_properties(${WOT_PROJECTILE_SOURCES}
        PROPERTIES COMPILE_OPTIONS "-std=c++17;-fno-fast-math;-ffp-contract=off")
endif()
