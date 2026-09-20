#include <tbb/blocked_range.h>
#include <tbb/parallel_for.h>
#include <tbb/parallel_reduce.h>
#include <tbb/scalable_allocator.h>

#include <cstdio>
#include <functional>
#include <numeric>
#include <vector>

int main() {
    const int count = 100000;
    std::vector<int> values(static_cast<std::size_t>(count));

    tbb::parallel_for(tbb::blocked_range<int>(0, count), [&](const tbb::blocked_range<int>& range) {
        for (int index = range.begin(); index != range.end(); ++index) {
            values[static_cast<std::size_t>(index)] = index + 1;
        }
    });

    const long long sum = tbb::parallel_reduce(
        tbb::blocked_range<int>(0, count), 0LL,
        [&](const tbb::blocked_range<int>& range, long long partial) {
            return std::accumulate(values.begin() + range.begin(),
                                 values.begin() + range.end(), partial);
        }, std::plus<long long>());

    tbb::scalable_allocator<int> allocator;
    int* allocated = allocator.allocate(32);
    for (int index = 0; index != 32; ++index) {
        allocated[index] = index;
    }
    const bool allocation_ok = allocated[31] == 31;
    allocator.deallocate(allocated, 32);

    const long long expected = static_cast<long long>(count) * (count + 1) / 2;
    if (sum != expected || !allocation_ok) {
        std::fprintf(stderr, "TBB smoke FAIL sum=%lld expected=%lld allocation=%d\n",
                     sum, expected, allocation_ok ? 1 : 0);
        return 1;
    }
    std::printf("TBB smoke PASS sum=%lld allocation=ok\n", sum);
    return 0;
}
