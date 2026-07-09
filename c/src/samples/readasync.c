/**
 * Sample program that reads tags in the background and prints the
 * tags found for M7e and M3e.
 * The code sample also demonstrates how to connect to M6E series modules
 * and read tags using the Mercury API v1.37.1. The M6E family users must modify their
 * application by referring this code sample in order to use the latest API version 
 * (a) To enable M6E compatible code, uncomment ENABLE_M6E_COMPATIBILITY macro.
 * (b) To enable standalone tag operation, uncomment ENABLE_TAGOP_PROTOCOL macro.
 * 
 * The code sample also demonstrates how to tune your reader dutycycle in case of 
 * TMR_ERROR_TEMPERATURE_RAISING_ALERT to a particular dutycycle.
 * 
 * @file readasync.c
 */
#include <serial_reader_imp.h>
#include <tm_reader.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <inttypes.h>
#ifndef BARE_METAL
#ifndef WIN32
#include <unistd.h>
#endif
#endif /* BARE_METAL */

/* Change total read time here */
#define READ_TIME 5000 //In ms
#define DEFAULT_READ_POWER 1900  /* centi-dBm */
#define ENABLE_STATS_LISTENER 0

/**@def ENABLE_TAGOP_PROTOCOL
 * To set tagOp protocol before performing tag operation.
 * By default, protocol is set to NONE on the M6e family.
 * Make sure to set Gen2 protocol before performing Gen2 standalone tag operation.
 */
#define ENABLE_TAGOP_PROTOCOL 0

#ifdef BARE_METAL
  #define printf(...) {}
#endif

#ifndef BARE_METAL
/* Enable this to use transportListener */
#ifndef USE_TRANSPORT_LISTENER
#define USE_TRANSPORT_LISTENER 0
#endif

#define usage() {errx(1, "Please provide valid reader URL, such as: reader-uri [--ant n] [--pow read_power]\n"\
                         "reader-uri : e.g., 'tmr:///COM1' or 'tmr:///dev/ttyS0/' or 'tmr://readerIP'\n"\
                         "[--ant n] : e.g., '--ant 1'\n"\
                         "[--pow read_power] : e.g, '--pow 1900' (centi-dBm)\n"\
                         "Example for UHF modules: 'tmr:///com4' or 'tmr:///com4 --ant 1,2 --pow 1900' \n"\
                         "Example for HF/LF modules: 'tmr:///com4' \n");}

static volatile uint16_t exceptionCnt;
static bool isStopReadSentInTempAlert = false;
#ifdef TMR_ENABLE_UHF
static bool isInventoryRoundDoneReport = false;
#endif /* TMR_ENABLE_UHF */

void errx(int exitval, const char *fmt, ...)
{
  va_list ap;

  va_start(ap, fmt);
  vfprintf(stderr, fmt, ap);

  exit(exitval);
}
#endif /* BARE_METAL */

void checkerr(TMR_Reader* rp, TMR_Status ret, int exitval, const char *msg)
{
#ifndef BARE_METAL
  if ((TMR_SUCCESS != ret) && (TMR_SUCCESS_STREAMING != ret))
  {
    errx(exitval, "Error %s: %s\n", msg, TMR_strerr(rp, ret));
  }
#endif /* BARE_METAL */
}

#ifdef USE_TRANSPORT_LISTENER
void serialPrinter(bool tx, uint32_t dataLen, const uint8_t data[],
                   uint32_t timeout, void *cookie)
{
  FILE *out = cookie;
  uint32_t i;

  fprintf(out, "%s", tx ? "Sending: " : "Received:");
  for (i = 0; i < dataLen; i++)
  {
    if (i > 0 && (i & 15) == 0)
      fprintf(out, "\n         ");
    fprintf(out, " %02x", data[i]);
  }
  fprintf(out, "\n");
}

void stringPrinter(bool tx,uint32_t dataLen, const uint8_t data[],uint32_t timeout, void *cookie)
{
  FILE *out = cookie;

  fprintf(out, "%s", tx ? "Sending: " : "Received:");
  fprintf(out, "%s\n", data);
}
#endif /* USE_TRANSPORT_LISTENER */

#ifndef BARE_METAL
void parseAntennaList(uint8_t *antenna, uint8_t *antennaCount, char *args)
{
  char *token = NULL;
  char *str = ",";
  uint8_t i = 0x00;
  int scans;

  /* get the first token */
  if (NULL == args)
  {
    fprintf(stdout, "Missing argument\n");
    usage();
  }

  token = strtok(args, str);
  if (NULL == token)
  {
    fprintf(stdout, "Missing argument after %s\n", args);
    usage();
  }

  while(NULL != token)
  {
#ifdef WIN32
      scans = sscanf(token, "%hh"SCNu8, &antenna[i]);
#else
      scans = sscanf(token, "%"SCNu8, &antenna[i]);
#endif
    if (1 != scans)
    {
      fprintf(stdout, "Can't parse '%s' as an 8-bit unsigned integer value\n", token);
      usage();
    }
    i++;
    token = strtok(NULL, str);
  }
  *antennaCount = i;
}

void exceptionHandler(TMR_Reader* reader);
#endif /* BARE_METAL */
void callback(TMR_Reader *reader, const TMR_TagReadData *t, void *cookie);
void exceptionCallback(TMR_Reader *reader, TMR_Status error, void *cookie);
#if ENABLE_STATS_LISTENER
void statsCallback(TMR_Reader* reader, const TMR_Reader_StatsValues* stats, void* cookie);
#endif
#ifdef TMR_ENABLE_UHF
void inventoryRoundSummaryCallback(TMR_Reader* reader, const TMR_InventoryRoundSummary* summary, void* cookie);
#endif /* TMR_ENABLE_UHF */

#ifdef SINGLE_THREAD_ASYNC_READ
uint32_t totalTagRcved = 0;
bool stopReadCommandSent = false;

TMR_Status
parseSingleThreadedResponse(TMR_Reader* rp, uint32_t readTime);
#endif /* SINGLE_THREAD_ASYNC_READ */

#if ENABLE_STATS_LISTENER && defined(TMR_ENABLE_UHF)
static char _protocolNameBuf[32];
static const char* protocolName(enum TMR_TagProtocol value)
{
    switch (value)
    {
    case TMR_TAG_PROTOCOL_NONE:
        return "NONE";
    case TMR_TAG_PROTOCOL_GEN2:
        return "GEN2";
#ifdef TMR_ENABLE_ISO180006B
    case TMR_TAG_PROTOCOL_ISO180006B:
        return "ISO180006B";
    case TMR_TAG_PROTOCOL_ISO180006B_UCODE:
        return "ISO180006B_UCODE";
#endif /* TMR_ENABLE_ISO180006B */
#ifndef TMR_ENABLE_GEN2_ONLY
    case TMR_TAG_PROTOCOL_IPX64:
        return "IPX64";
    case TMR_TAG_PROTOCOL_IPX256:
        return "IPX256";
    case TMR_TAG_PROTOCOL_ATA:
        return "ATA";
#endif /* TMR_ENABLE_GEN2_ONLY */
    case TMR_TAG_PROTOCOL_ISO14443A:
        return "ISO14443A";
    case TMR_TAG_PROTOCOL_ISO15693:
        return "ISO15693";
    case TMR_TAG_PROTOCOL_LF125KHZ:
        return "LF125KHZ";
    case TMR_TAG_PROTOCOL_LF134KHZ:
        return "LF134KHZ";
    default:
        snprintf(_protocolNameBuf, sizeof(_protocolNameBuf), "TagProtocol:%d", (int)value);
        return _protocolNameBuf;
    }
}
#endif /* ENABLE_STATS_LISTENER && TMR_ENABLE_UHF */

#if ENABLE_STATS_LISTENER
void parseReaderStats(const TMR_Reader_StatsValues* stats)
{
#ifdef TMR_ENABLE_UHF
    uint8_t i = 0;

    /** Each  field should be validated before extracting the value */
    if (TMR_READER_STATS_FLAG_CONNECTED_ANTENNAS & stats->valid)
    {
        printf("Antenna Connection Status\n");

        for (i = 0; i < stats->connectedAntennas.len; i += 2)
        {
            printf("Antenna %d |%s\n", stats->connectedAntennas.list[i],
                stats->connectedAntennas.list[i + 1] ? "connected" : "Disconnected");
        }
    }

    if (TMR_READER_STATS_FLAG_NOISE_FLOOR_SEARCH_RX_TX_WITH_TX_ON & stats->valid)
    {
        printf("Noise Floor With Tx On\n");

        for (i = 0; i < stats->perAntenna.len; i++)
        {
            printf("Antenna %d | %d db\n", stats->perAntenna.list[i].antenna,
                stats->perAntenna.list[i].noiseFloor);
        }
    }

    if (TMR_READER_STATS_FLAG_RF_ON_TIME & stats->valid)
    {
        printf("RF On Time\n");

        for (i = 0; i < stats->perAntenna.len; i++)
        {
            printf("Antenna %d | %d ms\n", stats->perAntenna.list[i].antenna,
                stats->perAntenna.list[i].rfOnTime);
        }
    }

    if (TMR_READER_STATS_FLAG_FREQUENCY & stats->valid)
    {
        printf("Frequency %d(khz)\n", stats->frequency);
    }
#endif /* TMR_ENABLE_UHF */

    if (TMR_READER_STATS_FLAG_TEMPERATURE & stats->valid)
    {
        printf("Temperature %d(C)\n", stats->temperature);
    }

#ifdef TMR_ENABLE_UHF
    if (TMR_READER_STATS_FLAG_PROTOCOL & stats->valid)
    {
        printf("Protocol %s\n", protocolName(stats->protocol));
    }

    if (TMR_READER_STATS_FLAG_ANTENNA_PORTS & stats->valid)
    {
        printf("currentAntenna %d\n", stats->antenna);
    }
#endif /* TMR_ENABLE_UHF */
}
#endif /* ENABLE_STATS_LISTENER */

static int duty_cycles[] = { 90, 80, 70, 60, 50, 40, 30, 20, 10}; // duty cycle percentages

int main(int argc, char *argv[])
{
  TMR_Reader r, *rp;
  TMR_Status ret;
  TMR_Region region;
  uint8_t buffer[20];
  uint8_t i;
  TMR_ReadPlan plan;
  TMR_ReadListenerBlock rlb;
  TMR_ReadExceptionListenerBlock reb;
#if ENABLE_STATS_LISTENER
  TMR_StatsListenerBlock slb;
#endif
#ifdef TMR_ENABLE_UHF
    TMR_InventoryRoundSummaryListenerBlock ilb;
#endif /* TMR_ENABLE_UHF */
  uint8_t *antennaList = NULL;
  uint8_t antennaCount = 0x0;
  int readPower = DEFAULT_READ_POWER;
  char string[100];
  TMR_String model;

#if USE_TRANSPORT_LISTENER
  TMR_TransportListenerBlock tb;
#endif /* USE_TRANSPORT_LISTENER */
  rp = &r;

#ifndef BARE_METAL
  if (argc < 2)
  {
    usage(); 
  }

  for (i = 2; i < argc; i+=2)
  {
    if(0x00 == strcmp("--ant", argv[i]))
    {
      if (NULL != antennaList)
      {
        fprintf(stdout, "Duplicate argument: --ant specified more than once\n");
        usage();
      }
      parseAntennaList(buffer, &antennaCount, argv[i+1]);
      antennaList = buffer;
    }
    else if (0x00 == strcmp("--pow", argv[i]))
    {
      long retval;
      char *startptr;
      char *endptr;
      startptr = argv[i+1];
      retval = strtol(startptr, &endptr, 0);
      if (endptr != startptr)
      {
        readPower = (int)retval;
      }
      else
      {
        fprintf(stdout, "Can't parse read power: %s\n", argv[i+1]);
        usage();
      }
    }
    else
    {
      fprintf(stdout, "Argument %s is not recognized\n", argv[i]);
      usage();
    }
  }

  ret = TMR_create(rp, argv[1]);
  checkerr(rp, ret, 1, "creating reader");
#else
  ret = TMR_create(rp, "tmr:///com1");
#ifdef TMR_ENABLE_UHF
  buffer[0] = 1;
  antennaList = buffer;
  antennaCount = 0x01;
#endif /* TMR_ENABLE_UHF */
#endif /* BARE_METAL */

#if USE_TRANSPORT_LISTENER

  if (TMR_READER_TYPE_SERIAL == rp->readerType)
  {
    tb.listener = serialPrinter;
  }
  else
  {
    tb.listener = stringPrinter;
  }
  tb.cookie = stdout;

  TMR_addTransportListener(rp, &tb);
#endif /* USE_TRANSPORT_LISTENER */

  ret = TMR_connect(rp);
  if ((TMR_READER_TYPE_SERIAL == rp->readerType) && (TMR_SUCCESS != ret))
  {
      /* MercuryAPI tries connecting to the module using default baud rate of 115200 bps.
       * The connection may fail if the module is configured to a different baud rate. If
       * that is the case, the MercuryAPI tries connecting to the module with other supported
       * baud rates until the connection is successful using baud rate probing mechanism.
       */
      if (TMR_ERROR_TIMEOUT == ret)
      {
          uint32_t currentBaudRate;

          /* Start probing mechanism. */
          ret = TMR_SR_cmdProbeBaudRate(rp, &currentBaudRate);
          checkerr(rp, ret, 1, "Probe the baudrate");

          /* Set the current baudrate, so that
           * next TMR_Connect() call can use this baudrate to connect.
           */
          TMR_paramSet(rp, TMR_PARAM_BAUDRATE, &currentBaudRate);
      }

      /* When the module is streaming the tags,
       * TMR_connect() returns with TMR_SUCCESS_STREAMING status, which should be handled in the codelet.
       * User can either continue to parse streaming responses or stop the streaming.
       * Use 'stream' option demonstrated in the AutonomousMode.c codelet to
       * continue streaming. To stop the streaming, use TMR_stopStreaming() as demonstrated below.
       */
      if (TMR_SUCCESS_STREAMING == ret)
      {
          ret = TMR_stopStreaming(rp);
          checkerr(rp, ret, 1, "Stoping the read");
      }

      if (TMR_SUCCESS == ret)
      {
          ret = TMR_connect(rp);
      }     
  }
  checkerr(rp, ret, 1, "Connecting reader");

  model.value = string;
  model.max   = sizeof(string);
  TMR_paramGet(rp, TMR_PARAM_VERSION_MODEL, &model);
  checkerr(rp, ret, 1, "Getting version model");

  if (0 != strcmp("M3e", model.value))
  {
    region = TMR_REGION_NONE;
    ret = TMR_paramGet(rp, TMR_PARAM_REGION_ID, &region);
    checkerr(rp, ret, 1, "getting region");

    if (TMR_REGION_NONE == region)
    {
      TMR_RegionList regions;
      TMR_Region _regionStore[32];
      regions.list = _regionStore;
      regions.max = sizeof(_regionStore)/sizeof(_regionStore[0]);
      regions.len = 0;

      ret = TMR_paramGet(rp, TMR_PARAM_REGION_SUPPORTEDREGIONS, &regions);
      checkerr(rp, ret, __LINE__, "getting supported regions");

      if (regions.len < 1)
      {
        checkerr(rp, TMR_ERROR_INVALID_REGION, __LINE__, "Reader doesn't supportany regions");
      }
      region = regions.list[0];
      ret = TMR_paramSet(rp, TMR_PARAM_REGION_ID, &region);
      checkerr(rp, ret, 1, "setting region");  
    }
  }

#if TMR_ENABLE_M6E_COMPATIBILITY
  {
    /* To make the latest API compatible with M6e family modules,
     * set below configurations.
     * 1. tagOp protocol:  This parameter is not needed for Continuous Read or Async Read, but it
     *                     must be set when standalone tag operation is performed, because the
     *                     protocol is set to NONE, by default, in the M6e family modules.
     *                     So, users must set Gen2 protocol prior to performing Gen2 standalone tag operation.
     * 2. Set read filter: To report repeated tag entries of same tag, users must disable read filter.
     *                     This filter is enabled, by default, in the M6e family modules.
     * 3. Metadata flag:   TMR_TRD_METADATA_FLAG_ALL includes all flags (Supported by UHF and HF/LF readers).
     *                     Disable unsupported flags for M6E family as shown below.
     * Note: tagOp protocol and read filter are one time configurations - These must be set on the module 
     *       once after every power ON.
     *       We do not have to set them in every read cycle.
     *       But the Metadata flag must be set once after establishing a connection to the module using TMR_connect().
     */
#if ENABLE_TAGOP_PROTOCOL
    {
      /* 1. tagOp protocol: This parameter is not needed for Continuous Read or Async Read, but it
      *                    must be set when standalone tag operation is performed, because the
      *                    protocol is set to NONE, by default, in the M6e family modules.
      *                    So, users must set Gen2 protocol prior to performing Gen2 standalone tag operation.
      */
      TMR_TagOp readop;
      TMR_TagProtocol protocol = TMR_TAG_PROTOCOL_GEN2;
      ret = TMR_paramSet(rp, TMR_PARAM_TAGOP_PROTOCOL, &protocol);
      checkerr(rp, ret, 1, "setting protocol");
   
      TMR_TagOp_init_GEN2_ReadData(&readop, TMR_GEN2_BANK_EPC, 0, 2);
      ret = TMR_executeTagOp(rp, &readop, NULL, NULL);
      checkerr(rp, ret, 1, "executing read data tag operation");
    }
#endif /* ENABLE_TAGOP_PROTOCOL */
    if ((TMR_READER_TYPE_LLRP != rp->readerType))
    {
      /* 2. Set read filter: To report repeated tag entries of same tag, users must disable read filter.
       *                     This filter is enabled, by default, in the M6e family modules.
       *                     Note that this is a one time configuration while connecting to the module after
       *                     power ON. We do not have to set it for every read cycle.
       */
      bool readFilter = false;
      ret = TMR_paramSet(rp, TMR_PARAM_TAGREADDATA_ENABLEREADFILTER, &readFilter);
      checkerr(rp, ret, 1, "setting read filter");
    }

    {
      /* 3. Metadata flag: TMR_TRD_METADATA_FLAG_ALL includes all flags (Supported by UHF and HF/LF readers).
       *                   Disable unsupported flags for M6e family as shown below.
       */
      TMR_TRD_MetadataFlag metadata = (uint16_t)(TMR_TRD_METADATA_FLAG_ALL & (~TMR_TRD_METADATA_FLAG_TAGTYPE));
      ret = TMR_paramSet(rp, TMR_PARAM_METADATAFLAG, &metadata);
      checkerr(rp, ret, 1, "Setting Metadata Flags");
    }
  }
#endif /* TMR_ENABLE_M6E_COMPATIBILITY */

  

#if ENABLE_STATS_LISTENER
    {
        /** Request for the reader stats fields of your interest, before search */
        TMR_Reader_StatsFlag setFlag = TMR_READER_STATS_FLAG_TEMPERATURE | TMR_READER_STATS_FLAG_ANTENNA_PORTS;

        ret = TMR_paramSet(rp, TMR_PARAM_READER_STATS_ENABLE, &setFlag);
        checkerr(rp, ret, 1, "setting the  fields");

    }
#endif
    if ((TMR_READER_TYPE_LLRP == rp->readerType))
    {
        TMR_TemperatureProActiveAlert* getTempAlert , * setTempAlert;

        setTempAlert = malloc(sizeof(TMR_TemperatureProActiveAlert));
        if (setTempAlert == NULL) {
            fprintf(stdout, "setTempAlert Memory allocation failed\n");
            checkerr(rp, ret, 1, "setTempAlert Memory allocation failed\n");
        }
        getTempAlert = malloc(sizeof(TMR_TemperatureProActiveAlert));
        if (getTempAlert == NULL) {
            fprintf(stdout, "getTempAlert Memory allocation failed\n");
            checkerr(rp, ret, 1, "getTempAlert Memory allocation failed\n");
        }
          
        /** Configure the Temperature alert values */
        setTempAlert->alertEnable = true;
        setTempAlert->alertThreshold = 75;
        setTempAlert->alertPeriod = 10000;

        // Set the param TMR_PARAM_RADIO_TEMPERATURE_ALERT
        printf("\nSetting temperature alert values: alertEnable %d , alertThreshold: %d (C) and alertPeriod: %d(ms)\n ", setTempAlert->alertEnable,
            setTempAlert->alertThreshold, setTempAlert->alertPeriod);
        ret = TMR_paramSet(rp, TMR_PARAM_RADIO_TEMPERATURE_ALERT, setTempAlert);
        checkerr(rp, ret, 1, "setting the temperature alert\n");

        // Get the param TMR_PARAM_RADIO_TEMPERATURE_ALERT
        ret = TMR_paramGet(rp, TMR_PARAM_RADIO_TEMPERATURE_ALERT, getTempAlert);
        checkerr(rp, ret, 1, "getting the temperature alert");
        printf("\nGetting the temperature alert values: alertEnable: %d\n", getTempAlert->alertEnable);
        if (getTempAlert->alertEnable)
        {
            printf("alertThreshold: %d (C)\n", getTempAlert->alertThreshold);
            printf("alertPeriod: %d (ms)\n", getTempAlert->alertPeriod);
        }
    }

#ifdef TMR_ENABLE_UHF
    /* Change to "if (1)" to request inventory round summary report while performing async read */
    if(0)
    {
        bool setSummaryFlag = true;
        ret = TMR_paramSet(rp, TMR_PARAM_READER_INVENTORY_SUMMARY_FLAG_ENABLE, &setSummaryFlag);
        checkerr(rp, ret, 1, "setting the  summary flag");
    }
#endif /* TMR_ENABLE_UHF */

    /**
    * for antenna configuration we need two parameters
    * 1. antennaCount : specifies the no of antennas should
    *    be included in the read plan, out of the provided antenna list.
    * 2. antennaList  : specifies  a list of antennas for the read plan.
    **/
    // initialize the read plan 
    if (0 != strcmp("M3e", model.value))
    {
        ret = TMR_RP_init_simple(&plan, antennaCount, antennaList, TMR_TAG_PROTOCOL_GEN2, 1000);
    }
    else
    {
        ret = TMR_RP_init_simple(&plan, antennaCount, antennaList, TMR_TAG_PROTOCOL_ISO14443A, 1000);
    }
    checkerr(rp, ret, 1, "initializing the read plan");

    /* Commit read plan */
    ret = TMR_paramSet(rp, TMR_PARAM_READ_PLAN, &plan);
    checkerr(rp, ret, 1, "setting read plan");

    if (0 != strcmp("M3e", model.value))
    {
      ret = TMR_paramSet(rp, TMR_PARAM_RADIO_READPOWER, &readPower);
      checkerr(rp, ret, 1, "setting read power");
    }

    rlb.listener = callback;
    rlb.cookie = NULL;

    reb.listener = exceptionCallback;
    reb.cookie = NULL;

#if ENABLE_STATS_LISTENER
    slb.listener = statsCallback;
    slb.cookie = NULL;
#endif

#ifdef TMR_ENABLE_UHF
    ilb.listener = inventoryRoundSummaryCallback;
    ilb.cookie = NULL;
#endif /* TMR_ENABLE_UHF */

    ret = TMR_addReadListener(rp, &rlb);
    checkerr(rp, ret, 1, "adding read listener");

    ret = TMR_addReadExceptionListener(rp, &reb);
    checkerr(rp, ret, 1, "adding exception listener");

#if ENABLE_STATS_LISTENER
    ret = TMR_addStatsListener(rp, &slb);
    checkerr(rp, ret, 1, "adding the stats listener");
#endif

#ifdef TMR_ENABLE_UHF
    ret = TMR_addInventoryRoundSummaryListener(rp, &ilb);
    checkerr(rp, ret, 1, "adding the InventoryRoundSummary listener");
#endif /* TMR_ENABLE_UHF */

    printf("............Starting the read............\n");
    ret = TMR_startReading(rp);
    checkerr(rp, ret, 1, "starting reading");

#ifndef SINGLE_THREAD_ASYNC_READ
    /* Exit the while loop,
    * 1. When error occurs
    * 2. When sleep timeout expires
    */

    while (1)
    {
        /* Exit the process if any error occurred */
        if (rp->lastReportedException != TMR_SUCCESS)
        {
            exceptionHandler(rp);
        }

        /* If exception is handled, restart the read with the tuned duty cycle.. */
        if (isStopReadSentInTempAlert)
        {
            printf("............Restarting the read with tuned duty cycle............\n");

            // Restarting the read with modified value of async off time
            ret = TMR_startReading(rp);
            checkerr(rp, ret, 1, "Re-starting the read");
            isStopReadSentInTempAlert = false;
        }

#ifdef TMR_ENABLE_UHF
        if (isInventoryRoundDoneReport)
        {
            printf("\n!!!Stopping the read !!!\n");
            ret = TMR_stopReading(rp);
            checkerr(rp, ret, 1, "stop reading");


            printf("\n............Restarting the read............\n");
            ret = TMR_startReading(rp);
            checkerr(rp, ret, 1, "Re-starting the read");
            isInventoryRoundDoneReport = false;
        }
#endif /* TMR_ENABLE_UHF */

#ifndef WIN32
        usleep(1);
#else
        Sleep(1);
#endif
    }
#else
    parseSingleThreadedResponse(rp, READ_TIME);
#endif /* SINGLE_THREAD_ASYNC_READ */

}

void
callback(TMR_Reader *reader, const TMR_TagReadData *t, void *cookie)
{
  char epcStr[128];
  char timeStr[128];

  TMR_bytesToHex(t->tag.epc, t->tag.epcByteCount, epcStr);
#ifndef BARE_METAL
  TMR_getTimeStamp(reader, t, timeStr);
#endif /* BARE_METAL */
  printf("Background read: Tag ID:%s ant:%d count:%d ", epcStr, t->antenna, t->readCount);
  printf("time:%s\n", timeStr);

  /* Reset the variable for valid tag response. */
  reader->lastReportedException = 0;
}

void 
exceptionCallback(TMR_Reader *reader, TMR_Status error, void *cookie)
{
  if(reader->lastReportedException != error)
  {
#ifndef BARE_METAL
    fprintf(stdout, "Error:%s\n", TMR_strerr(reader, error));
#endif /* BARE_METAL */
    // Increment the exceptionCnt when this error is seen..
    if (error == TMR_ERROR_TEMPERATURE_RAISING_ALERT)
    {
        exceptionCnt += 1;
    }
  }
  reader->lastReportedException = error;
}

#if ENABLE_STATS_LISTENER
void statsCallback(TMR_Reader* reader, const TMR_Reader_StatsValues* stats, void* cookie)
{
#ifndef BARE_METAL
    parseReaderStats(stats);
#endif /* BARE_METAL */
}
#endif /* ENABLE_STATS_LISTENER */

#ifdef TMR_ENABLE_UHF
void inventoryRoundSummaryCallback(TMR_Reader* reader, const TMR_InventoryRoundSummary* summary, void* cookie)
{
    printf("num_slots:  %d\n", summary->num_slots);
    printf("empty_slots:  %d\n", summary->empty_slots);
    printf("single_slots:  %d\n", summary->single_slots);
    printf("collided_slots:  %d\n", summary->collided_slots);

    isInventoryRoundDoneReport = true;
}
#endif /* TMR_ENABLE_UHF */

#ifndef BARE_METAL
void exceptionHandler(TMR_Reader *reader)
{
  TMR_Status ret = TMR_SUCCESS;
  switch(reader->lastReportedException)
  {
    case TMR_ERROR_MSG_INVALID_PARAMETER_VALUE:
    case TMR_ERROR_UNIMPLEMENTED_FEATURE:
    {
      ret = TMR_stopReading(reader);
      checkerr(reader, ret, 1, "stopping reading");
      TMR_destroy(reader);
      exit(1);
    }
    case TMR_ERROR_TIMEOUT:
    {
      TMR_flush(reader);
      TMR_destroy(reader);
      exit(1);
    }
    case TMR_ERROR_BUFFER_OVERFLOW:
    {
      printf("!!! API buffer overflow occurred. It may be due to more processing delay in the read listener. !!!\n");
      printf("!!! As part of recovery mechanism, API will stop and restart the read. !!!\n\n");
      printf("To avoid the error :\n1) It is advisable keep read listener as faster as possible.\n2) Increase queue slot size by modifying TMR_MAX_QUEUE_SLOTS macro value in tm_config.h file.\n\n");
      break;
    }
    case TMR_ERROR_SYSTEM_UNKNOWN_ERROR:
    case TMR_ERROR_TM_ASSERT_FAILED:
    case TMR_ERROR_UNSUPPORTED:
    {
      TMR_destroy(reader);
      exit(1);
    }
    case TMR_ERROR_TEMPERATURE_RAISING_ALERT:
    {
        //In case of this TMR_ERROR_TEMPERATURE_RAISING_ALERT. Do the following....
        // 1. Stop the read. 
        // 2. Set the duty cycle -- by setting async off time !!! 
        // 3.!!! Start the read again with this duty cycle -- main() does this !!!
        if (exceptionCnt < 10)
        {
            uint32_t asyncOnTime = 0;
            ret = TMR_stopReading(reader);
            checkerr(reader, ret, 1, "stopping reading");
            printf("\n!!!Stopping the read !!!\n");
            

            //Get async on time
            ret = TMR_paramGet(reader, TMR_PARAM_READ_ASYNCONTIME, &asyncOnTime);
            checkerr(reader, ret, 1, "getting the async on time");

            // Calculate async Off time
            uint32_t offtime = (asyncOnTime * 100 / duty_cycles[exceptionCnt - 1]) - asyncOnTime;

            // Set async off time
            ret = TMR_paramSet(reader, TMR_PARAM_READ_ASYNCOFFTIME, &offtime);
            checkerr(reader, ret, 1, "setting the async off time");
            printf("!!! Tuning the duty cycle to %d percentage. Setting Async Off Time to %d ms. !!!\n",
                duty_cycles[exceptionCnt - 1], offtime);
            isStopReadSentInTempAlert = true;
        }
        else
        {
            // If this error is seen even after handling, you need to tune your duty cycle according to your use-case.
            // Please reach out to support for any heat sink options.
            printf("!!! Tune the reader dutycycle based on your usecase. !!!\n");
            printf("!!! Even after tuning the duty cycle, if you still see this error, please reach out to rfid-support@jadaktech.com for heat sink options. !!!\n");
        }
        break;
    }
    default:
    {
      break;
    }
  }
  reader->lastReportedException = 0; // reset it... after handling
}
#endif /* BARE_METAL */

#ifdef SINGLE_THREAD_ASYNC_READ
TMR_Status
parseSingleThreadedResponse(TMR_Reader* rp, uint32_t readTime)
{
  TMR_Status ret = TMR_SUCCESS;
  uint32_t elapsedTime = 0;
  uint64_t startTime = tmr_gettime();

  while (true)
  {
    TMR_TagReadData trd;

    ret = TMR_hasMoreTags(rp);
    if (TMR_SUCCESS == ret)
    {
      TMR_getNextTag(rp, &trd);
      notify_read_listeners(rp, &trd);
      totalTagRcved++;
    }
    else if (TMR_ERROR_END_OF_READING == ret)
    {
      break;
    }
    else
    {
      if ((TMR_ERROR_NO_TAGS != ret) && (TMR_ERROR_NO_TAGS_FOUND != ret))
      {
        notify_exception_listeners(rp, ret);
      }
    }

    elapsedTime = tmr_gettime() - startTime;

    if ((elapsedTime > readTime) && (!stopReadCommandSent))
    {
      ret = TMR_stopReading(rp);
      if (TMR_SUCCESS == ret)
      {
        stopReadCommandSent = true;
      }
    }
  }
  reset_continuous_reading(rp);
  
  return TMR_SUCCESS;
}
#endif /* SINGLE_THREAD_ASYNC_READ */
