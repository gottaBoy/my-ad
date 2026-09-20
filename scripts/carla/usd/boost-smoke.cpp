#include <boost/atomic.hpp>
#include <boost/chrono.hpp>
#include <boost/filesystem.hpp>
#include <boost/iostreams/device/mapped_file.hpp>
#include <boost/iostreams/stream.hpp>
#include <boost/program_options.hpp>
#include <boost/regex.hpp>
#include <boost/system/error_code.hpp>
#include <boost/thread.hpp>
#include <boost/version.hpp>
#include <cstdio>
#include <fstream>
#include <stdexcept>

#ifndef _LIBCPP_VERSION
#error "UE libc++ is required"
#endif
static_assert(BOOST_VERSION == 108200, "Boost 1.82.0 required");

static void require(bool value, const char* message)
{
    if (!value)
        throw std::runtime_error(message);
}

int main(int argc, char** argv)
{
    try
    {
        require(argc == 2, "output path required");
        const auto start = boost::chrono::steady_clock::now();
        boost::atomic<int> count(0);
        boost::thread thread([&count] { count.fetch_add(42); });
        thread.join();
        require(count.load() == 42, "thread/atomic failed");
        namespace po = boost::program_options;
        po::options_description options("test");
        options.add_options()("answer", po::value<int>());
        const char* args[] = {"smoke", "--answer", "42"};
        po::variables_map values;
        po::store(po::parse_command_line(3, args, options), values);
        po::notify(values);
        require(values["answer"].as<int>() == 42, "program_options failed");
        require(boost::regex_match(std::string("arm64-42"), boost::regex("arm[0-9]+-[0-9]+")), "regex failed");
        boost::filesystem::path path(argv[1]);
        require(!boost::filesystem::exists(path), "refusing overwrite");
        { std::ofstream file(path.string()); file << "boost-native-42\n"; }
        require(boost::filesystem::file_size(path) == 16, "filesystem size");
        boost::iostreams::mapped_file_source mapping(path.string());
        require(mapping.is_open() && std::string(mapping.data(), mapping.size()) == "boost-native-42\n", "iostreams mapped_file");
        mapping.close();
        boost::system::error_code error = make_error_code(boost::system::errc::invalid_argument);
        require(bool(error) && !error.message().empty(), "system error category");
        require(boost::chrono::steady_clock::now() >= start, "chrono failed");
        std::puts("Boost 1.82.0 native atomic/chrono/filesystem/iostreams/program_options/regex/system/thread PASS");
        return 0;
    }
    catch (const std::exception& error)
    {
        std::fprintf(stderr, "Boost smoke FAIL: %s\n", error.what());
        return 2;
    }
}
