targetScope = 'resourceGroup'
@minLength(3)
@maxLength(19)
param prefix string
param location string = 'norwayeast'
param nodeSpecs array
param allNodes array
param roleImages object
param gatewayImage string
param operatorPublicKey string
param shutdownAt string
param expiresAt string
var tags = { project: 'DAWL', pilot: prefix, expiresAt: expiresAt, cloudVcpus: '3', layout: 'one-container-per-node' }
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: '${prefix}acr' }
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: '${prefix}state' }
resource files 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' existing = { name: 'default', parent: storage }
resource archives 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = [for node in nodeSpecs: {
  name: 'nodes-${node.name}'
  parent: files
  properties: { shareQuota: 1, enabledProtocols: 'SMB' }
}]
resource pullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = { name: '${prefix}-pull' }
var nodeContainers = [for node in nodeSpecs: {
      name: node.name
      properties: {
        image: roleImages[node.role]
        resources: { requests: { cpu: node.cpu, memoryInGB: node.memory_gb }, limits: { cpu: 3, memoryInGB: node.memory_gb } }
        environmentVariables: [
          { name: 'DAWL_NAME', value: node.name }
          { name: 'DAWL_HOST', value: node.host }
          { name: 'DAWL_SITE', value: 'cloud' }
          { name: 'DAWL_IMAGE_PIN', value: roleImages[node.role] }
          { name: 'DAWL_DEADLINE', value: shutdownAt }
        ]
        volumeMounts: [
          { name: 'state-${node.name}', mountPath: '/node' }
          { name: 'archive-${node.name}', mountPath: '/persist' }
          { name: 'exchange', mountPath: '/exchange' }
        ]
      }
    }]
var stateVolumes = [for node in nodeSpecs: { name: 'state-${node.name}', emptyDir: {} }]
var archiveVolumes = [for node in nodeSpecs: { name: 'archive-${node.name}', share: 'nodes-${node.name}' }]
var volumeSpecs = concat([{ name: 'exchange' }], stateVolumes, archiveVolumes)
resource pool 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: '${prefix}-nodes'
  location: location
  tags: tags
  dependsOn: [archives]
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${pullIdentity.id}': {} } }
  properties: {
    osType: 'Linux'
    restartPolicy: 'Never'
    sku: 'Standard'
    imageRegistryCredentials: [{ server: registry.properties.loginServer, identity: pullIdentity.id }]
    ipAddress: { type: 'Public', ports: [{ port: 2222, protocol: 'TCP' }], dnsNameLabel: '${prefix}-nodes' }
    containers: concat(nodeContainers,[{
      name: 'gateway'
      properties: {
        image: gatewayImage
        resources: { requests: { cpu: json('0.5'), memoryInGB: json('0.5') }, limits: { cpu: 3, memoryInGB: json('0.5') } }
        ports: [{ port: 2222, protocol: 'TCP' }]
        environmentVariables: [
          { name: 'DAWL_DEADLINE', value: shutdownAt }
          { name: 'DAWL_OPERATOR_PUBLIC_KEY', value: operatorPublicKey }
          { name: 'DAWL_NODES', value: string(allNodes) }
        ]
        volumeMounts: [{ name: 'exchange', mountPath: '/exchange' }]
      }
    }])
    volumes: [for volume in volumeSpecs: contains(volume,'share') ? {
      name: volume.name
      azureFile: {
        shareName: volume.share
        storageAccountName: storage.name
        storageAccountKey: storage.listKeys().keys[0].value
      }
    } : { name: volume.name, emptyDir: {} }]
  }
}
resource deadline 'Microsoft.Logic/workflows@2019-05-01' = {
  name: '${prefix}-nodes-deadline'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      triggers: { deadline: { type: 'Recurrence', recurrence: { frequency: 'Minute', interval: 5 } } }
      actions: {
        expired: {
          type: 'If'
          expression: { greaterOrEquals: ['@utcNow()', expiresAt] }
          actions: {
            stop: {
              type: 'Http'
              inputs: {
                method: 'POST'
                uri: '${environment().resourceManager}${substring(pool.id,1)}/stop?api-version=2023-05-01'
                authentication: { type: 'ManagedServiceIdentity' }
              }
            }
          }
          else: { actions: {} }
        }
      }
      outputs: {}
    }
  }
}
resource stopRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(pool.id,deadline.id,'stop')
  scope: pool
  properties: {
    principalId: deadline.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions','b24988ac-6180-42a0-ab88-20f7382dd24c')
  }
}
output poolName string = pool.name
output fqdn string = pool.properties.ipAddress.fqdn
