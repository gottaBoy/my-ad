#include <cstdio>

#ifdef TBB_STATIC_SMOKE_DRIVER

extern "C" int tbb_static_smoke();
int main()
{
    const int code = tbb_static_smoke();
    if (code) std::fprintf(stderr, "TBB whole-archive smoke FAIL check=%d\n", code);
    else std::puts("TBB 2019u8 whole-archive smoke PASS");
    return code;
}

#else

#include <tbb/blocked_range.h>
#include <tbb/global_control.h>
#include <tbb/parallel_for.h>
#include <tbb/parallel_reduce.h>
#include <tbb/scalable_allocator.h>
#include <tbb/task_scheduler_init.h>
#include <tbb/task_scheduler_observer.h>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <functional>
#include <numeric>
#include <vector>

#ifndef _LIBCPP_VERSION
#error "UE libc++ headers are required"
#endif
#if !defined(__aarch64__) || __SIZEOF_POINTER__ != 8
#error "Native AArch64 LP64 is required"
#endif
#if TBB_USE_EXCEPTIONS
#error "Match the UE Linux TBB_USE_EXCEPTIONS=0 contract"
#endif
static_assert(TBB_VERSION_MAJOR == 2019 && TBB_VERSION_MINOR == 0
              && TBB_INTERFACE_VERSION == 11008, "TBB 2019u8 interface required");

static bool no_dynamic_tbb()
{
    std::FILE* stream = std::fopen("/proc/self/maps", "r");
    if (!stream) return false;
    bool valid = true;
    char line[4096];
    while (std::fgets(line, sizeof(line), stream))
    {
        if (std::strstr(line, "/libtbb.so") || std::strstr(line, "/libtbb_debug.so")
            || std::strstr(line, "/libtbbmalloc.so") || std::strstr(line, "/libirml.so")
            || std::strstr(line, "/libtbbmalloc_debug.so"))
        {
            std::fprintf(stderr, "Unexpected dynamic TBB mapping: %s", line);
            valid = false;
        }
    }
    if (std::ferror(stream)) valid = false;
    std::fclose(stream);
    return valid;
}

class WorkerObserver : public tbb::task_scheduler_observer
{
public:
    std::atomic<unsigned> entries{0};
    void on_scheduler_entry(bool worker) override
    {
        if (worker) entries.fetch_add(1, std::memory_order_relaxed);
    }
};

extern "C" int tbb_static_smoke()
{
    if (!no_dynamic_tbb()) return 1;
    if (tbb::TBB_runtime_interface_version() != TBB_INTERFACE_VERSION) return 2;
    unsigned observed = 0;
    {
        tbb::global_control limit(tbb::global_control::max_allowed_parallelism, 4);
        tbb::task_scheduler_init scheduler(4);
        WorkerObserver observer;
        observer.observe(true);
        const int count = 100000;
        std::vector<int> values(count, -1);
        std::atomic<unsigned> errors{0};
        tbb::parallel_for(tbb::blocked_range<int>(0, count, 256),
            [&](const tbb::blocked_range<int>& range) {
                for (int i = range.begin(); i != range.end(); ++i)
                {
                    unsigned char* block = static_cast<unsigned char*>(scalable_malloc(256));
                    if (!block) { ++errors; continue; }
                    const unsigned char expected = static_cast<unsigned char>(i);
                    std::memset(block, expected, 256);
                    if (block[0] != expected || block[255] != expected
                        || scalable_msize(block) < 256) ++errors;
                    scalable_free(block);
                    values[i] = i + 1;
                }
            });
        const long long sum = tbb::parallel_reduce(tbb::blocked_range<int>(0, count),
            0LL, [&](const tbb::blocked_range<int>& range, long long partial) {
                return std::accumulate(values.begin() + range.begin(),
                                       values.begin() + range.end(), partial);
            }, std::plus<long long>());
        observer.observe(false);
        observed = observer.entries.load();
        if (errors.load() || sum != 5000050000LL || observed == 0) return 3;

        unsigned char* small = static_cast<unsigned char*>(scalable_calloc(32, 1));
        if (!small) return 4;
        for (int i = 0; i < 32; ++i)
            if (small[i] != 0) { scalable_free(small); return 5; }
        std::memset(small, 0x5a, 32);
        unsigned char* large = static_cast<unsigned char*>(scalable_realloc(small, 32768));
        if (!large) { scalable_free(small); return 6; }
        bool preserved = scalable_msize(large) >= 32768;
        for (int i = 0; i < 32; ++i) preserved = preserved && large[i] == 0x5a;
        scalable_free(large);
        if (!preserved) return 7;

        void* aligned = scalable_aligned_malloc(4096, 64);
        if (!aligned) return 8;
        const bool alignment_ok = reinterpret_cast<std::uintptr_t>(aligned) % 64 == 0;
        std::memset(aligned, 0x3c, 4096);
        const bool content_ok = static_cast<unsigned char*>(aligned)[4095] == 0x3c;
        scalable_aligned_free(aligned);
        if (!alignment_ok || !content_ok) return 9;
    }
    if (!no_dynamic_tbb()) return 10;
    std::printf("{\"interface_version\":11008,\"sum\":5000050000,"
                "\"parallel_allocations\":100000,\"worker_entries\":%u,"
                "\"calloc_realloc\":true,\"alignment\":64,\"dynamic_tbb_loaded\":false}\n",
                observed);
    return 0;
}

#ifndef TBB_STATIC_SMOKE_LIBRARY
int main()
{
    const int code = tbb_static_smoke();
    if (code) std::fprintf(stderr, "TBB static smoke FAIL check=%d\n", code);
    else std::puts("TBB 2019u8 static PIC smoke PASS");
    return code;
}
#endif

#endif
