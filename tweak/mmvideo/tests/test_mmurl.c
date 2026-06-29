#include "../mmurl.h"
#include <string.h>
#include <assert.h>
#include <stdio.h>
int main(void){
    char out[512];
    // localhost cache URL -> file:// in the cache dir
    assert(mm_url_to_path("http://127.0.0.1:8080/seg_abc_0.mp4", out, sizeof out) == 1);
    assert(strcmp(out, "file:///var/mobile/Media/MosaicMeshCache/seg_abc_0.mp4") == 0);
    // full_ asset likewise
    assert(mm_url_to_path("http://127.0.0.1:8080/full_def_0.mp4", out, sizeof out) == 1);
    assert(strcmp(out, "file:///var/mobile/Media/MosaicMeshCache/full_def_0.mp4") == 0);
    // non-localhost passes through (return 0)
    assert(mm_url_to_path("http://192.168.1.60:3000/media/server/videos/x.mp4", out, sizeof out) == 0);
    // path traversal in name is rejected (return 0)
    assert(mm_url_to_path("http://127.0.0.1:8080/../../etc/passwd", out, sizeof out) == 0);
    printf("ok\n"); return 0;
}
