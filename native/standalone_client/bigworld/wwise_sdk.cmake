# Licensed SDK inputs are supplied privately by the owner and never packaged.
file(TO_CMAKE_PATH "${WOT_WWISE_SDK_ROOT}" WOT_WWISE_SDK_ROOT)
if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
    message(FATAL_ERROR "The restored Wwise backend requires the Win32 SDK")
endif()
if(NOT EXISTS "${WOT_WWISE_SDK_ROOT}/include/AK/AkWwiseSDKVersion.h")
    message(FATAL_ERROR "WOT_WWISE_SDK_ROOT must contain the licensed Wwise 2017.1.9 SDK")
endif()

add_library(WotWwiseSdk INTERFACE)
target_include_directories(WotWwiseSdk INTERFACE
    "${WOT_WWISE_SDK_ROOT}/include"
    "${WOT_WWISE_SDK_ROOT}/samples/SoundEngine/Common"
    "${WOT_WWISE_SDK_ROOT}/samples/SoundEngine/Win32")
target_compile_definitions(WotWwiseSdk INTERFACE AK_OPTIMIZED UNICODE _UNICODE)
foreach(library IN ITEMS
        AkSoundEngine AkMemoryMgr AkMusicEngine AkStreamMgr
        AkVorbisDecoder AkSineSource AkSilenceSource AkParametricEQFX
        AkDelayFX AkCompressorFX AkExpanderFX AkMatrixReverbFX AkRoomVerbFX
        AkFlangerFX AkGuitarDistortionFX AkConvolutionReverbFX AkMeterFX
        AkTremoloFX AkStereoDelayFX AkPitchShifterFX AkHarmonizerFX AkGainFX
        McDSPLimiterFX McDSPFutzBoxFX iZTrashMultibandDistortionFX)
    set(path "${WOT_WWISE_SDK_ROOT}/Win32_vc150/Release/lib/${library}.lib")
    if(NOT EXISTS "${path}")
        message(FATAL_ERROR "The licensed SDK input lacks ${library}.lib")
    endif()
    target_link_libraries(WotWwiseSdk INTERFACE "${path}")
endforeach()
target_link_libraries(WotWwiseSdk INTERFACE dsound dxguid winmm ws2_32 ole32 user32)
