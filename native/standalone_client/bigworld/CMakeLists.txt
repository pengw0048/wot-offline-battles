# Included from the pinned BigWorld client/CMakeLists.txt after bwclient exists.
if(NOT TARGET bwclient)
    message(FATAL_ERROR "The standalone application must be linked into bwclient")
endif()

set(WOT_NUMERICAL_SOURCES
    "${CMAKE_CURRENT_LIST_DIR}/projectile_scene.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/vehicle_scene.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/target_scene.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/projectile_motion.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/vehicle_control.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/combat_rules.cpp"
    "${CMAKE_CURRENT_LIST_DIR}/../src/vehicle_collision.cpp")
target_sources(bwclient PRIVATE "${CMAKE_CURRENT_LIST_DIR}/standalone_app.cpp"
    ${WOT_NUMERICAL_SOURCES})
target_include_directories(bwclient PRIVATE "${BW_SOURCE_DIR}/client"
    "${CMAKE_CURRENT_LIST_DIR}/../include")
# These sources are appended after the legacy client's PCH source enumeration.
# Keep its C++14/PCH contract intact; the recovered numerical core uses C++17.
if(MSVC)
    set_source_files_properties(${WOT_NUMERICAL_SOURCES}
        PROPERTIES COMPILE_OPTIONS "/std:c++17;/fp:strict")
    # These adapters include the public engine's C++14 STL declarations.
    # Retain that observed surface in their otherwise C++17 translation units.
    set_source_files_properties(
        "${CMAKE_CURRENT_LIST_DIR}/projectile_scene.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/vehicle_scene.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/target_scene.cpp"
        PROPERTIES COMPILE_DEFINITIONS
        "_HAS_AUTO_PTR_ETC=1;_HAS_TR1_NAMESPACE=1;_ENFORCE_MATCHING_ALLOCATORS=0")
    # The bundled Python 2.7 declarations still use register parameters.
    # Keep that legacy-header warning local to the engine adapters.
    set_property(SOURCE
        "${CMAKE_CURRENT_LIST_DIR}/projectile_scene.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/vehicle_scene.cpp"
        "${CMAKE_CURRENT_LIST_DIR}/target_scene.cpp"
        APPEND PROPERTY COMPILE_OPTIONS "/wd5033")
else()
    set_source_files_properties(${WOT_NUMERICAL_SOURCES}
        PROPERTIES COMPILE_OPTIONS "-std=c++17;-fno-fast-math;-ffp-contract=off")
endif()
