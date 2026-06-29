#ifndef MMURL_H
#define MMURL_H
#include <string.h>
#include <stddef.h>
#include <stdio.h>
#define MM_CACHE_DIR "/var/mobile/Media/MosaicMeshCache/"
#define MM_LOCALHOST "http://127.0.0.1:8080/"
static int mm_name_is_safe(const char *n){
    if(!*n) return 0;
    for(const char *p=n; *p; ++p){ if(*p=='/'||*p=='\\') return 0; }
    if(strstr(n,"..")) return 0;
    return 1;
}
static int mm_url_to_path(const char *url, char *out, size_t outlen){
    if(out && outlen) out[0]=0;
    size_t pl = strlen(MM_LOCALHOST);
    if(strncmp(url, MM_LOCALHOST, pl) != 0) return 0;
    const char *name = url + pl;
    const char *q = strpbrk(name, "?#");
    char nbuf[256]; size_t nl = q ? (size_t)(q-name) : strlen(name);
    if(nl >= sizeof nbuf) return 0;
    memcpy(nbuf, name, nl); nbuf[nl]=0;
    if(!mm_name_is_safe(nbuf)) return 0;
    int n = snprintf(out, outlen, "file://%s%s", MM_CACHE_DIR, nbuf);
    return (n>0 && (size_t)n < outlen) ? 1 : 0;
}
#endif
