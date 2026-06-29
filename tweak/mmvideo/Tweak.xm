#import <substrate.h>
#import <stdio.h>
#import <unistd.h>
static void mmlog(const char *m){ FILE*f=fopen("/tmp/mmvideo.log","a"); if(f){fprintf(f,"%s\n",m);fclose(f);} }
%ctor { char b[128]; snprintf(b,sizeof(b),"[mmvideo] loaded pid=%d", getpid()); mmlog(b); }
