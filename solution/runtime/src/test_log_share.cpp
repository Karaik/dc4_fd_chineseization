#include "log_file.h"

int wmain(int argc, wchar_t** argv) {
    if (argc != 2) return 1;
    FILE* log = open_log_file(argv[1]);
    if (!log) return 2;
    fputs("LIVE log writer\n", log);
    fflush(log);
    puts("READY");
    fflush(stdout);
    int command = getchar();
    fclose(log);
    return command == '\n' ? 0 : 3;
}
