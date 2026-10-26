#include <GFx.h>
#include <GFx/AMP/Amp_Server.h>
#include <cstdio>
#include <cstring>
#include <windows.h>

int main(int argc, char ** argv)
{
    // The opt-in mode reproduces the old public-manager startup/teardown
    // sequence against the real SDK for native crash diagnosis.
    const bool reproduce = argc == 2 &&
        std::strcmp(argv[1], "--reproduce-premature-uninit") == 0;
    for (unsigned pass = 0; pass != 3; ++pass)
    {
        {
            Scaleform::GFx::System system;
            Scaleform::AmpServer * owner = &Scaleform::AmpServer::GetInstance();
            {
                Scaleform::Ptr<Scaleform::GFx::AMP::ServerState> state =
                    *new Scaleform::GFx::AMP::ServerState;
                state->StateFlags = Scaleform::GFx::AMP::Amp_Disabled;
                owner->UpdateState(state);
            }
            if (reproduce) Scaleform::AmpServer::Uninit();
            Scaleform::AmpServer & current = Scaleform::AmpServer::GetInstance();
            Scaleform::Log::GetDefaultLog();
            if (&current != owner)
            {
                std::puts("AMP owner replaced by a static fallback before SDK teardown");
                std::fflush(stdout);
                // The real crash dump establishes that SDK teardown releases
                // this fallback as a Server. Fail before that invalid release.
                TerminateProcess(GetCurrentProcess(), 2);
                return 2;
            }
            (void)current.GetCurrentState();
            std::printf("AMP disabled lifecycle pass %u: owner preserved=%d\n",
                pass, &current == owner ? 1 : 0);
            std::fflush(stdout);
        }
        if (Scaleform::Memory::GetGlobalHeap() != NULL) return 3;
    }
    std::puts("Real Scaleform disabled profiler teardown and reinitialization passed");
    return 0;
}
